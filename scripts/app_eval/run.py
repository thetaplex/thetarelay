"""Build each app spec with several setups and compare hidden-test results and cost.

    uv run python scripts/app_eval/run.py run --runs 3 --specs bookmarks --arms claude,pi
    uv run python scripts/app_eval/run.py report

Specs live in specs/<name>/ (starter/, hidden_tests/, reference/); `--specs` picks some
(default: all).

Arms:
  claude    Claude Code (Opus 5.5) + Headroom proxy (ANTHROPIC_BASE_URL from your settings) + RTK
            hook, with your normal Claude Code configuration.
  pi-<arm>  Pi + RTK extension -> ThetaRelay (Headroom middleware on) -> Haiku 5.5 (lite) or
            Opus 5.5 (frontier), both on OpenRouter. Clean Pi config, no skills. <arm> is any
            routing eval arm (pi-hybrid, pi-judge, pi-random-hybrid, pi-hybrid-no-triage, ...);
            `pi` is an alias of pi-hybrid. Random arms take the lite share of their routed arm's
            turn logs for the same spec. Override models with THETARELAY_FRONTIER_MODEL,
            THETARELAY_LITE_MODEL and so on.

Each build starts from a copy of the spec's starter/ in a fresh directory. Afterwards the hidden
tests are copied in and run. Results go to results/builds.jsonl, keyed by (spec, arm, run);
builds, raw agent output and turn logs (results/<spec>/turns-<arm>-<run>.jsonl) are kept too.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing_eval import run as routing

HERE = Path(__file__).resolve().parent
SPECS = HERE / "specs"
REPO = HERE.parents[1]
RESULTS = Path(os.environ.get("APP_EVAL_RESULTS", HERE / "results"))
PORT = 8795
TIMEOUT = 30 * 60
PROMPT = (
    "Build the application described in SPEC.md in this directory. Use uv for Python "
    "(`uv run ...`). Write your own tests in tests/ and make sure they pass. Work autonomously "
    "until the application is complete; do not ask questions."
)
PI_ENV = {
    "THETARELAY_FRONTIER_MODEL": os.environ.get(
        "THETARELAY_FRONTIER_MODEL", "anthropic/claude-opus-5.5"
    ),
    "THETARELAY_LITE_BASE_URL": os.environ.get(
        "THETARELAY_LITE_BASE_URL", "https://openrouter.ai/api"
    ),
    "THETARELAY_LITE_MODEL": os.environ.get("THETARELAY_LITE_MODEL", "anthropic/claude-haiku-5.5"),
    "THETARELAY_LITE_API_KEY": os.environ.get(
        "THETARELAY_LITE_API_KEY", os.environ.get("OPENROUTER_API_KEY", "")
    ),
    "THETARELAY_EASY_THRESHOLD": os.environ.get("THETARELAY_EASY_THRESHOLD", "0.7"),
    "THETARELAY_STAY_REQUESTS": os.environ.get("THETARELAY_STAY_REQUESTS", "0"),
    "THETARELAY_HEADROOM_MIDDLEWARE": "true",
}


def select_specs(names: str, root: Path | None = None) -> list[Path]:
    """Spec folders under `root` (default SPECS), all of them or the comma-separated `names`."""
    root = root or SPECS
    specs = sorted(p for p in root.iterdir() if (p / "starter").is_dir())
    if not names:
        return specs
    wanted = set(names.split(","))
    if unknown := wanted - {p.name for p in specs}:
        raise SystemExit(f"unknown specs: {', '.join(sorted(unknown))}")
    return [p for p in specs if p.name in wanted]


def canonical_arm(arm: str) -> str:
    return "pi-hybrid" if arm == "pi" else arm


def load_builds() -> list[dict[str, Any]]:
    """Rows of builds.jsonl that have a spec; older rows did not record their route mode."""
    log = RESULTS / "builds.jsonl"
    rows = list(map(json.loads, log.read_text().splitlines())) if log.exists() else []
    if old := sum("spec" not in r for r in rows):
        print(f"ignoring {old} old rows without a spec in {log}", flush=True)
    return [r for r in rows if "spec" in r]


def arm_env(arm: str, spec_results: Path) -> dict[str, str] | None:
    """Proxy environment for a pi arm; None when a random arm's routed arm has no turn logs."""
    base = canonical_arm(arm).removeprefix("pi-")
    if not arm.startswith("pi") or base not in routing.ARMS:
        raise SystemExit(f"unknown arm: {arm}")
    return routing.arm_env(base, spec_results, prefix="pi-")


def fresh_workdir(name: str, spec: Path) -> Path:
    work = Path(tempfile.mkdtemp(prefix=f"app-eval-{name}-"))
    shutil.copytree(spec / "starter", work, dirs_exist_ok=True)
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


def start_proxy(arm_env: dict[str, str], turn_log: Path) -> subprocess.Popen[bytes]:
    env = {**os.environ, **PI_ENV, **arm_env, "THETARELAY_TURN_LOG": str(turn_log)}
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


def run_pi(work: Path, out: Path, env: dict[str, str], turn_log: Path) -> dict[str, Any]:
    proxy = start_proxy(env, turn_log)
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
            stdin=subprocess.DEVNULL,  # `pi -p` waits for stdin to close
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
    # Triage mode: the frontier call that answered <EASY/> before a lite request.
    rec["triage_cost_usd"] = round(sum(r.get("triage_cost_usd") or 0 for r in rows), 4)
    rec["api_cost_usd"] = round(
        rec["frontier"]["cost_usd"] + rec["lite"]["cost_usd"] + rec["triage_cost_usd"], 4
    )
    return rec


def grade(work: Path, spec: Path) -> dict[str, Any]:
    shutil.copytree(spec / "hidden_tests", work / "tests_hidden", dirs_exist_ok=True)

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
    done = {(r["spec"], canonical_arm(r["arm"]), r["run"]) for r in load_builds()}
    arms = [canonical_arm(a) for a in args.arms.split(",")]
    for arm in arms:  # fail on a typo now, not after a 30-minute build
        if arm != "claude":
            arm_env(arm, RESULTS)
    for spec in select_specs(args.specs):
        spec_results = RESULTS / spec.name
        spec_results.mkdir(exist_ok=True)
        for run_no in range(args.runs):
            for arm in arms:
                if (spec.name, arm, run_no) in done:
                    continue
                env = None if arm == "claude" else arm_env(arm, spec_results)
                if arm != "claude" and env is None:
                    print(f"skipping {arm} on {spec.name}: no turn logs for its routed arm yet")
                    continue
                out = spec_results / f"{arm}-{run_no}"
                out.mkdir(exist_ok=True)
                work = fresh_workdir(f"{spec.name}-{arm}-{run_no}", spec)
                print(
                    f"{time.strftime('%H:%M:%S')} start {spec.name} {arm} run {run_no}  ({work})",
                    flush=True,
                )
                if env is None:
                    rec = run_claude(work, out)
                else:
                    turn_log = spec_results / f"turns-{arm}-{run_no}.jsonl"
                    rec = run_pi(work, out, env, turn_log)
                rec.update(grade(work, spec))
                shutil.copytree(
                    work,
                    out / "build",
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.db", "*.sqlite"),
                )
                shutil.rmtree(work, ignore_errors=True)
                rec = {
                    "spec": spec.name,
                    "arm": arm,
                    "run": run_no,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    **rec,
                    **(env or {}),
                }
                with log.open("a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                done.add((spec.name, arm, run_no))
                total = rec["hidden_passed"] + max(rec["hidden_failed"], 0)
                print(
                    f"{time.strftime('%H:%M:%S')} done  {spec.name} {arm} run {run_no}: hidden "
                    f"{rec['hidden_passed']}/{total}, {rec['seconds']:.0f}s, "
                    f"api-price ${rec.get('api_cost_usd') or 0:.3f}, {rec['status']}",
                    flush=True,
                )


def build_report(builds: list[dict[str, Any]], turns: dict[str, list[dict[str, Any]]]) -> str:
    """The routing eval's report over builds, pairing routed and random arms by (spec, run).

    A build passes when all its hidden tests pass. Arm `pi` counts as `pi-hybrid`.
    """
    attempts = [
        {
            "arm": canonical_arm(b["arm"]),
            "run": b["run"],
            "exercise": b["spec"],
            "passed": b["hidden_passed"] > 0 and b["hidden_failed"] == 0,
            "cost_usd": b.get("api_cost_usd") or 0,
        }
        for b in builds
    ]
    arms = ["claude", *(f"pi-{a}" for a in routing.ARMS)]
    return routing.build_report(attempts, turns, arms, prefix="pi-")


def report(_: argparse.Namespace) -> None:
    builds = load_builds()
    turns: dict[str, list[dict[str, Any]]] = {}
    for p in RESULTS.glob("*/turns-*.jsonl"):
        arm, run_no = p.stem.removeprefix("turns-").rsplit("-", 1)
        rows = [{**json.loads(ln), "run": int(run_no)} for ln in p.read_text().splitlines()]
        turns.setdefault(arm, []).extend(rows)
    print(build_report(builds, turns))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--arms", default="claude,pi")
    r.add_argument("--specs", default="", help="comma-separated spec names (default: all)")
    sub.add_parser("report")
    args = parser.parse_args()
    {"run": run, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()
