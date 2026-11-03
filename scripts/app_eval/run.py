"""Build the SPEC.md app with two setups and compare hidden-test results and token use.

    uv run python scripts/app_eval/run.py run --runs 3
    uv run python scripts/app_eval/run.py report

Arms:
  claude  Claude Code (Opus 5.5) + Headroom proxy (ANTHROPIC_BASE_URL from your settings) + RTK
          hook, with your normal Claude Code configuration.
  pi      Pi + RTK extension -> ThetaRelay (Headroom middleware on) -> Qwen 3.6 35B (lite,
          OpenRouter) or Opus 5.5 (frontier, OpenRouter). Clean Pi config, no skills.

Each build starts from a copy of starter/ in a fresh directory. Afterwards the hidden tests are
copied in and run. Builds, raw agent output and turn logs are kept in results/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RESULTS = HERE / "results"
PORT = 8795
TIMEOUT = 30 * 60
PROMPT = (
    "Build the application described in SPEC.md in this directory. Use uv for Python "
    "(`uv run ...`). Write your own tests in tests/ and make sure they pass. Work autonomously "
    "until the application is complete; do not ask questions."
)
PI_ENV = {
    "THETARELAY_FRONTIER_MODEL": "anthropic/claude-opus-5.5",
    "THETARELAY_LITE_BASE_URL": "https://openrouter.ai/api",
    "THETARELAY_LITE_MODEL": "qwen/qwen3.6-35b-a3b",
    "THETARELAY_LITE_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
    "THETARELAY_EASY_THRESHOLD": "0.8",
    "THETARELAY_STAY_REQUESTS": "1",
    "THETARELAY_HEADROOM_MIDDLEWARE": "true",
}


def fresh_workdir(name: str) -> Path:
    work = Path(tempfile.mkdtemp(prefix=f"app-eval-{name}-"))
    shutil.copytree(HERE / "starter", work, dirs_exist_ok=True)
    return work


def run_claude(work: Path, out: Path) -> dict[str, Any]:
    cmd = [
        "claude",
        "-p",
        PROMPT,
        "--model",
        "claude-opus-5-5",
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Bash Read Write Edit Glob Grep",
    ]
    started = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=TIMEOUT)
        status = "ok" if p.returncode == 0 else f"exit {p.returncode}"
        (out / "claude.json").write_text(p.stdout)
        (out / "claude.stderr").write_text(p.stderr)
    except subprocess.TimeoutExpired:
        status = "timeout"
    secs = time.perf_counter() - started
    rec: dict[str, Any] = {"status": status, "seconds": round(secs, 1)}
    try:
        data = json.loads((out / "claude.json").read_text())
    except (OSError, ValueError):
        return rec
    u = data.get("usage") or {}
    rec.update(
        requests=data.get("num_turns"),
        input_uncached=u.get("input_tokens", 0),
        cache_write=u.get("cache_creation_input_tokens", 0),
        cache_read=u.get("cache_read_input_tokens", 0),
        output=u.get("output_tokens", 0),
        api_cost_usd=data.get("total_cost_usd"),
        models={
            m: {k: v for k, v in mu.items() if "Tokens" in k or k == "costUSD"}
            for m, mu in (data.get("modelUsage") or {}).items()
        },
    )
    return rec


def start_proxy(turn_log: Path) -> subprocess.Popen[bytes]:
    env = {**os.environ, **PI_ENV, "THETARELAY_TURN_LOG": str(turn_log)}
    proc = subprocess.Popen(
        ["uv", "run", "thetarelay", "proxy", "--port", str(PORT)],
        cwd=REPO,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1)
            return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("proxy did not start")


def pi_agent_dir() -> tuple[Path, Path]:
    """A clean Pi config pointed at the proxy, plus the RTK extension generated into it."""
    home = Path(tempfile.mkdtemp(prefix="pi-app-eval-"))
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True)
    (agent / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "thetarelay": {
                        "baseUrl": f"http://127.0.0.1:{PORT}/v1",
                        "api": "openai-completions",
                        "apiKey": "unused",
                        "models": [{"id": "auto"}],
                    }
                }
            }
        )
    )
    (agent / "settings.json").write_text(json.dumps({"enabledModels": ["thetarelay/*"]}))
    subprocess.run(
        ["rtk", "init", "-g", "--agent", "pi"],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        check=True,
    )
    return agent, agent / "extensions" / "rtk.ts"


def run_pi(work: Path, out: Path) -> dict[str, Any]:
    turn_log = out / "turns.jsonl"
    proxy = start_proxy(turn_log)
    agent, rtk_ext = pi_agent_dir()
    started = time.perf_counter()
    try:
        p = subprocess.run(
            [
                "pi",
                "-p",
                "--no-session",
                "--no-skills",
                "--no-extensions",
                "-e",
                str(rtk_ext),
                "--model",
                "thetarelay/auto",
                PROMPT,
            ],
            cwd=work,
            env={**os.environ, "PI_CODING_AGENT_DIR": str(agent), "PI_OFFLINE": "1"},
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        status = "ok" if p.returncode == 0 else f"exit {p.returncode}"
        (out / "pi.stdout").write_text(p.stdout)
        (out / "pi.stderr").write_text(p.stderr)
    except subprocess.TimeoutExpired:
        status = "timeout"
    finally:
        proxy.terminate()
        proxy.wait()
        shutil.rmtree(agent.parent.parent, ignore_errors=True)
    secs = time.perf_counter() - started
    rows = [json.loads(ln) for ln in turn_log.read_text().splitlines()] if turn_log.exists() else []
    rec: dict[str, Any] = {"status": status, "seconds": round(secs, 1), "requests": len(rows)}
    for tier in ("frontier", "lite"):
        t = [r for r in rows if r["tier"] == tier]
        inp = sum(r.get("input_tokens") or 0 for r in t)
        cached = sum(r.get("cached_tokens") or 0 for r in t)
        rec[tier] = {
            "requests": len(t),
            "input_total": inp,
            "cache_read": cached,
            "output": sum(r.get("output_tokens") or 0 for r in t),
            "cost_usd": round(sum(r.get("cost_usd") or 0 for r in t), 4),
        }
    rec["api_cost_usd"] = round(rec["frontier"]["cost_usd"] + rec["lite"]["cost_usd"], 4)
    return rec


def grade(work: Path) -> dict[str, Any]:
    hidden = work / "tests_hidden"
    hidden.mkdir(exist_ok=True)
    shutil.copy(HERE / "hidden_tests" / "test_hidden_api.py", hidden)

    def pytest(target: str) -> tuple[int, int]:
        try:
            p = subprocess.run(
                ["uv", "run", "-q", "pytest", target, "-q", "-p", "no:cacheprovider"],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return 0, -1
        text = p.stdout + p.stderr
        passed = sum(int(n) for n in re.findall(r"(\d+) passed", text))
        failed = sum(int(n) for n in re.findall(r"(\d+) (?:failed|error)", text))
        return passed, failed

    hp, hf = pytest("tests_hidden")
    op, of = pytest("tests") if (work / "tests").exists() else (0, 0)
    loc = {
        d: sum(len(f.read_text(errors="ignore").splitlines()) for f in (work / d).rglob("*.py"))
        if (work / d).exists()
        else 0
        for d in ("app", "tests")
    }
    return {
        "hidden_passed": hp,
        "hidden_failed": hf,
        "own_passed": op,
        "own_failed": of,
        "loc_app": loc["app"],
        "loc_tests": loc["tests"],
    }


def run(args: argparse.Namespace) -> None:
    RESULTS.mkdir(exist_ok=True)
    log = RESULTS / "builds.jsonl"
    done = (
        {(r["arm"], r["run"]) for r in map(json.loads, log.read_text().splitlines())}
        if log.exists()
        else set()
    )
    for run_no in range(args.runs):
        for arm in args.arms.split(","):
            if (arm, run_no) in done:
                continue
            out = RESULTS / f"{arm}-{run_no}"
            out.mkdir(exist_ok=True)
            work = fresh_workdir(f"{arm}-{run_no}")
            print(f"{time.strftime('%H:%M:%S')} start {arm} run {run_no}  ({work})", flush=True)
            rec = (run_claude if arm == "claude" else run_pi)(work, out)
            rec.update(grade(work))
            shutil.copytree(
                work,
                out / "build",
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.db", "*.sqlite"),
            )
            shutil.rmtree(work, ignore_errors=True)
            rec = {"arm": arm, "run": run_no, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **rec}
            with log.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(
                f"{time.strftime('%H:%M:%S')} done  {arm} run {run_no}: hidden "
                f"{rec['hidden_passed']}/34, {rec['seconds']:.0f}s, "
                f"api-price ${rec.get('api_cost_usd') or 0:.3f}, {rec['status']}",
                flush=True,
            )


def report(_: argparse.Namespace) -> None:
    for r in map(json.loads, (RESULTS / "builds.jsonl").read_text().splitlines()):
        print(json.dumps(r))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--arms", default="claude,pi")
    sub.add_parser("report")
    args = parser.parse_args()
    {"run": run, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()
