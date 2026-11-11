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
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval_stats import MIN_RUNS, bootstrap_mean, bootstrap_paired_diff, newcombe, wilson

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


def turn_logs(results: Path, arm: str) -> list[Path]:
    """Turn logs of exactly this arm (`hybrid` must not match `hybrid-no-stop`)."""
    pat = rf"turns-{re.escape(arm)}-\d+\.jsonl"
    return sorted(p for p in results.glob("turns-*.jsonl") if re.fullmatch(pat, p.name))


def arm_env(arm: str, results: Path, prefix: str = "") -> dict[str, str] | None:
    """Proxy environment for an arm; None when a random arm's routed arm has no turn logs.

    `prefix` names the routed arm's turn logs (the app eval's arms are `pi-<arm>`).
    """
    env = dict(ARMS[arm])
    if arm.startswith("random"):
        routed = "judge" if arm == "random" else arm.removeprefix("random-")
        share = local_share(turn_logs(results, prefix + routed))
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


def _ci(lo: float | None, hi: float | None, scale: float = 1, fmt: str = ".1f") -> str:
    return "" if lo is None or hi is None else f" [{lo * scale:{fmt}}, {hi * scale:{fmt}}]"


def _run_costs(att: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[int, float]:
    """Cost per task of each run: that run's cost over its attempted tasks.

    The cost comes from the attempts' own `cost_usd` when they all carry one (app eval
    builds), otherwise from the turn logs, including frontier triage calls that handed the
    step back (`triage_cost_usd`) and discarded lite replies (`discarded_lite_cost_usd`).
    """
    tasks: dict[int, int] = defaultdict(int)
    for r in att:
        tasks[r["run"]] += 1
    own = all("cost_usd" in r for r in att)
    cost: dict[int, float] = defaultdict(float)
    for t in att if own else rows:
        cost[t["run"]] += sum(
            t.get(k) or 0 for k in ("cost_usd", "triage_cost_usd", "discarded_lite_cost_usd")
        )
    return {run: cost[run] / n for run, n in tasks.items() if run in cost}


def build_report(
    attempts: list[dict[str, Any]],
    turns: dict[str, list[dict[str, Any]]],
    arms: Iterable[str] = ARMS,
    prefix: str = "",
) -> str:
    """Per-arm table plus routed-vs-random paired differences, paired by `(run, exercise)`.

    `turns` maps arm -> turn-log rows, each tagged with its `run`. `prefix` is put before the
    routed and random arm names when pairing them (the app eval's arms are `pi-<arm>`).
    """
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in attempts:
        by_arm[r["arm"]].append(r)
    out = [f"{'arm':22}{'runs':>5}{'pass %':>8}  {'$/task':<22}{'lite %':>7}"]
    stats: dict[str, tuple[dict[tuple[int, str], bool], dict[int, float], int]] = {}
    for arm in arms:
        att = by_arm.get(arm)
        if not att:
            continue
        passed = {(r["run"], r["exercise"]): r["passed"] for r in att}
        costs = _run_costs(att, turns.get(arm, []))
        nruns = len({r["run"] for r in att})
        stats[arm] = (passed, costs, nruns)
        few = nruns < MIN_RUNS
        k = sum(passed.values())
        lo, hi = (None, None) if few else wilson(k, len(passed))
        line = f"{arm:22}{nruns:>5}{100 * k / len(passed):>7.1f}%{_ci(lo, hi, 100)}"
        if costs:
            m, clo, chi = bootstrap_mean(list(costs.values()))
            line += f"  ${m:.3f}{_ci(clo, chi, 1, '.3f')}"
        rows = turns.get(arm, [])
        if rows:
            line += f"  {100 * sum(t['tier'] == 'lite' for t in rows) / len(rows):.0f}%"
        out.append(line + ("  too few runs" if few else ""))
    out += ["", "routed vs matched random arm (difference, 95% interval)"]
    for base in ("judge", "triage", "hybrid"):
        routed, rnd = prefix + base, f"{prefix}random-{base}"
        if rnd not in stats and base == "judge":
            rnd = f"{prefix}random"  # alias of random-judge
        if routed not in stats or rnd not in stats:
            continue
        (pa, ca, na), (pb, cb, nb) = stats[routed], stats[rnd]
        keys = sorted(set(pa) & set(pb))
        runs = sorted(set(ca) & set(cb))
        unpaired = len(set(pa) ^ set(pb))
        few = min(na, nb) < MIN_RUNS
        line = f"{routed} vs {rnd}: "
        if keys:
            d, lo, hi = newcombe([pa[k] for k in keys], [pb[k] for k in keys])
            line += f"pass {100 * d:+.1f} pp" + ("" if few else _ci(lo, hi, 100, "+.1f"))
        if runs:
            d, lo, hi = bootstrap_paired_diff([ca[r] for r in runs], [cb[r] for r in runs])
            line += f"  cost {d:+.3f} $/task" + _ci(lo, hi, 1, "+.3f")
        out.append(
            line + f"  ({len(keys)} pairs, {unpaired} unpaired)" + ("  too few runs" if few else "")
        )
    return "\n".join(out)


def report(_: argparse.Namespace) -> None:
    attempts = [json.loads(ln) for ln in (RESULTS / "attempts.jsonl").read_text().splitlines()]
    turns: dict[str, list[dict[str, Any]]] = {}
    for arm in ARMS:
        for p in turn_logs(RESULTS, arm):
            run_no = int(p.stem.rsplit("-", 1)[1])
            rows = [{**json.loads(ln), "run": run_no} for ln in p.read_text().splitlines()]
            turns.setdefault(arm, []).extend(rows)
    print(build_report(attempts, turns))


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
