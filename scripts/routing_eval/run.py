"""Run Pi through ThetaRelay on Aider Polyglot Python exercises, once per routing arm.

Usage:
    uv run python scripts/routing_eval/run.py run --arms frontier,judge,random,lite --runs 1
    uv run python scripts/routing_eval/run.py report

Each attempt: copy the exercise (stub + tests + instructions) to a fresh directory, run
`pi -p` with a clean Pi config pointed at the proxy, then restore the original test file and
grade with pytest. The agent may run the tests itself (agentic setting, unlike Aider's
two-try protocol). Results go to `results/attempts.jsonl`; each arm's turn log to
`results/turns-<arm>-<run>.jsonl`.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval_stats import newcombe

HERE = Path(__file__).resolve().parent
RESULTS = Path(os.environ.get("ROUTING_EVAL_RESULTS", HERE / "results"))
BENCH = Path.home() / ".cache/thetarelay/polyglot-benchmark/python/exercises/practice"
PORT = 8790
ARMS: dict[str, dict[str, str]] = {
    "frontier": {"THETARELAY_ROUTE_MODE": "frontier"},
    "judge": {"THETARELAY_ROUTE_MODE": "judge"},
    "triage": {"THETARELAY_ROUTE_MODE": "triage"},
    "hybrid": {"THETARELAY_ROUTE_MODE": "hybrid"},
    # Random arms: the lite share is added from the routed arm's turn logs, see arm_env().
    "random": {"THETARELAY_ROUTE_MODE": "random"},  # alias of random-judge
    "random-judge": {"THETARELAY_ROUTE_MODE": "random"},
    "random-triage": {"THETARELAY_ROUTE_MODE": "random"},
    "random-hybrid": {"THETARELAY_ROUTE_MODE": "random"},
    "lite": {"THETARELAY_ROUTE_MODE": "lite"},
    "hybrid-no-stop": {"THETARELAY_ROUTE_MODE": "hybrid", "THETARELAY_ESCALATE_ON_STOP": "0"},
    "hybrid-no-triage": {"THETARELAY_ROUTE_MODE": "judge"},
    "hybrid-no-size-limit": {
        "THETARELAY_ROUTE_MODE": "hybrid",
        "THETARELAY_LITE_MAX_REQUEST_CHARS": "1000000000",
    },
}
# Aider's polyglot benchmark wording, plus where the instructions and tests are.
PROMPT = (
    "Use the instructions in .docs/ to modify {solution}. Keep and implement the existing "
    "function stubs. Use only standard libraries; do not suggest installing any packages. "
    "Don't change the names of existing functions or classes, as they may be referenced from "
    "other code like unit tests. You can run the tests with: python -m pytest -q"
)


def exercise_files(ex: Path) -> tuple[list[str], list[str]]:
    files = json.loads((ex / ".meta/config.json").read_text())["files"]
    return files["solution"], files["test"]


def start_proxy(env: dict[str, str], turn_log: Path) -> subprocess.Popen[bytes]:
    full = {
        **os.environ,
        **env,
        "THETARELAY_TURN_LOG": str(turn_log),
        "THETARELAY_HEADROOM_MIDDLEWARE": "false",
    }
    proc = subprocess.Popen(
        ["uv", "run", "thetarelay", "proxy", "--port", str(PORT)],
        env=full,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1)
            return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    sys.exit("proxy did not start")


def pi_config_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="pi-eval-agent-"))
    (d / "models.json").write_text(
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
    (d / "settings.json").write_text(json.dumps({"enabledModels": ["thetarelay/*"]}))
    return d


def attempt(ex: Path, agent_dir: Path, timeout: int) -> dict[str, Any]:
    solution, tests = exercise_files(ex)
    with tempfile.TemporaryDirectory(prefix=f"eval-{ex.name}-") as tmp:
        work = Path(tmp)
        shutil.copytree(ex, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".meta"))
        started = time.perf_counter()
        try:
            pi = subprocess.run(
                [
                    "pi",
                    "-p",
                    "--no-session",
                    "--no-skills",  # user-global skills would leak into every arm
                    "--no-extensions",
                    "--model",
                    "thetarelay/auto",
                    PROMPT.format(solution=", ".join(solution)),
                ],
                cwd=work,
                env={**os.environ, "PI_CODING_AGENT_DIR": str(agent_dir), "PI_OFFLINE": "1"},
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            agent_status = "ok" if pi.returncode == 0 else f"exit {pi.returncode}"
        except subprocess.TimeoutExpired:
            agent_status = "timeout"
        agent_s = time.perf_counter() - started
        for t in tests:  # grade against the pristine tests
            shutil.copy(ex / t, work / t)
        try:
            graded = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                cwd=work,
                capture_output=True,
                timeout=120,
                check=False,
            )
            passed = graded.returncode == 0
        except subprocess.TimeoutExpired:
            passed = False
    return {"agent_status": agent_status, "agent_s": round(agent_s, 1), "passed": passed}


def local_share(turn_logs: list[Path]) -> float | None:
    rows = [json.loads(ln) for p in turn_logs if p.exists() for ln in p.read_text().splitlines()]
    return sum(r["tier"] == "lite" for r in rows) / len(rows) if rows else None


def arm_env(arm: str, results: Path) -> dict[str, str] | None:
    """Proxy environment for an arm; None when a random arm's routed arm has no turn logs."""
    env = dict(ARMS[arm])
    if arm.startswith("random"):
        routed = "judge" if arm == "random" else arm.removeprefix("random-")
        share = local_share(sorted(results.glob(f"turns-{routed}-*.jsonl")))
        if share is None:
            return None
        env["THETARELAY_RANDOM_LITE_SHARE"] = f"{share:.3f}"
    return env


def run(args: argparse.Namespace) -> None:
    RESULTS.mkdir(exist_ok=True)
    exercises = sorted(p for p in BENCH.iterdir() if p.is_dir())
    if args.exercises:
        exercises = [ex for ex in exercises if ex.name in args.exercises.split(",")]
    elif args.limit:
        exercises = random.Random(0).sample(exercises, args.limit)
    out = RESULTS / "attempts.jsonl"
    done = set()
    if out.exists():
        done = {
            (r["arm"], r["run"], r["exercise"])
            for r in map(json.loads, out.read_text().splitlines())
        }
    for run_no in range(args.runs):
        for arm in args.arms.split(","):
            env = arm_env(arm, RESULTS)
            if env is None:
                print(f"skipping {arm}: no turn logs for its routed arm yet", flush=True)
                continue
            todo = [ex for ex in exercises if (arm, run_no, ex.name) not in done]
            if not todo:
                continue
            proxy = start_proxy(env, RESULTS / f"turns-{arm}-{run_no}.jsonl")
            agent_dir = pi_config_dir()
            try:
                for ex in todo:
                    res = attempt(ex, agent_dir, args.timeout)
                    rec = {
                        "arm": arm,
                        "run": run_no,
                        "exercise": ex.name,
                        **res,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        **env,
                    }
                    with out.open("a") as fh:
                        fh.write(json.dumps(rec) + "\n")
                    print(
                        f"{arm:8} run {run_no} {ex.name:28} {'PASS' if res['passed'] else 'fail'}"
                        f"  {res['agent_s']:6.0f}s  {res['agent_status']}",
                        flush=True,
                    )
            finally:
                proxy.terminate()
                proxy.wait()
                shutil.rmtree(agent_dir, ignore_errors=True)


def report(_: argparse.Namespace) -> None:
    attempts = [json.loads(ln) for ln in (RESULTS / "attempts.jsonl").read_text().splitlines()]
    by_arm: dict[str, dict[tuple[int, str], bool]] = defaultdict(dict)
    secs: dict[str, list[float]] = defaultdict(list)
    for r in attempts:
        by_arm[r["arm"]][(r["run"], r["exercise"])] = r["passed"]
        secs[r["arm"]].append(r["agent_s"])
    print(
        f"{'arm':9}{'pass':>11}{'requests':>10}{'lite %':>9}{'cost $':>9}"
        f"{'$/task':>8}{'cache %':>9}{'s/task':>8}  vs frontier (95% CI)"
    )
    base = by_arm.get("frontier", {})
    for arm in ARMS:
        if arm not in by_arm:
            continue
        res = by_arm[arm]
        turns = [
            json.loads(ln)
            for p in sorted(RESULTS.glob(f"turns-{arm}-*.jsonl"))
            for ln in p.read_text().splitlines()
        ]
        front = [t for t in turns if t["tier"] == "frontier"]
        cost = sum(t.get("cost_usd") or 0 for t in front)
        inp = sum(t.get("input_tokens") or 0 for t in front)
        cached = sum(t.get("cached_tokens") or 0 for t in front)
        lite = sum(t["tier"] == "lite" for t in turns)
        line = (
            f"{arm:9}{sum(res.values()):>5}/{len(res):<5}{len(turns):>10}"
            f"{100 * lite / max(len(turns), 1):>8.0f}%{cost:>9.2f}{cost / len(res):>8.3f}"
            f"{100 * cached / max(inp, 1):>8.0f}%{sum(secs[arm]) / len(secs[arm]):>8.0f}"
        )
        keys = sorted(set(res) & set(base))
        if arm != "frontier" and keys:
            d, lo, hi = newcombe([res[k] for k in keys], [base[k] for k in keys])
            line += f"  {100 * d:+.1f} pp [{100 * lo:+.1f}, {100 * hi:+.1f}]"
        print(line)
    reasons: dict[str, int] = defaultdict(int)
    for p in RESULTS.glob("turns-judge-*.jsonl"):
        for ln in p.read_text().splitlines():
            t = json.loads(ln)
            reasons[f"{t['reason'].split(' (')[0]} -> {t['tier']}"] += 1
    if reasons:
        print("\njudge arm decisions:", dict(sorted(reasons.items(), key=lambda kv: -kv[1])))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arms", default="frontier,judge,random,lite")
    r.add_argument("--runs", type=int, default=1)
    r.add_argument("--exercises", default="", help="comma-separated exercise names")
    r.add_argument("--limit", type=int, default=0, help="random subset of exercises (seed 0)")
    r.add_argument("--timeout", type=int, default=180, help="seconds per attempt")
    sub.add_parser("report")
    args = parser.parse_args()
    {"run": run, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()
