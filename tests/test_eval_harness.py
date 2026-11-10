import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec
    assert spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


stats = _load("eval_stats")


def test_wilson_7_of_10() -> None:
    lo, hi = stats.wilson(7, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.3968, 0.8922)


def test_newcombe_known_table() -> None:
    # n11=6, n10=4, n01=2, n00=8; values captured from routing_eval.run.newcombe before the move
    a = [True] * 10 + [False] * 10
    b = [True] * 6 + [False] * 4 + [True] * 2 + [False] * 8
    d, lo, hi = stats.newcombe(a, b)
    assert (d, lo, hi) == (
        0.09999999999999998,
        -0.12551488405825256,
        0.30837713606235473,
    )


def test_bootstrap_mean_deterministic() -> None:
    v = [1.0, 2.0, 3.0, 4.0, 10.0]
    m, lo, hi = stats.bootstrap_mean(v)
    assert m == 4.0
    assert lo is not None
    assert hi is not None
    assert 1.0 <= lo < m < hi <= 10.0
    assert stats.bootstrap_mean(v) == (m, lo, hi)


def test_bootstrap_paired_diff() -> None:
    a = [3.0, 4.0, 5.0, 6.0]
    b = [1.0, 2.0, 3.0, 4.0]
    d, lo, hi = stats.bootstrap_paired_diff(a, b)
    assert (d, lo, hi) == (2.0, 2.0, 2.0)


def test_fewer_than_min_runs_gives_no_interval() -> None:
    assert stats.MIN_RUNS == 3
    assert stats.bootstrap_mean([1.0, 2.0]) == (1.5, None, None)
    assert stats.bootstrap_paired_diff([2.0, 3.0], [1.0, 1.0]) == (1.5, None, None)


def _load_run() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "routing_eval_run", SCRIPTS / "routing_eval" / "run.py"
    )
    assert spec
    assert spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["routing_eval_run"] = mod
    spec.loader.exec_module(mod)
    return mod


routing = _load_run()


def _turns(results: Path, arm: str, lite: int, frontier: int) -> None:
    tiers = ["lite"] * lite + ["frontier"] * frontier
    (results / f"turns-{arm}-0.jsonl").write_text("".join(f'{{"tier": "{t}"}}\n' for t in tiers))


def test_hybrid_sends_40_percent_lite(tmp_path: Path) -> None:
    _turns(tmp_path, "hybrid", 2, 3)
    env = routing.arm_env("random-hybrid", tmp_path)
    assert env == {
        "THETARELAY_ROUTE_MODE": "random",
        "THETARELAY_RANDOM_LITE_SHARE": "0.400",
    }


def test_random_alias_uses_judge_logs(tmp_path: Path) -> None:
    _turns(tmp_path, "judge", 1, 2)
    env = routing.arm_env("random", tmp_path)
    assert env["THETARELAY_RANDOM_LITE_SHARE"] == "0.333"


def test_routed_arm_not_run_yet_is_skipped(tmp_path: Path) -> None:
    assert routing.arm_env("random-triage", tmp_path) is None


def test_ablation_settings() -> None:
    hybrid = routing.ARMS["hybrid"]
    assert routing.ARMS["hybrid-no-stop"] == {**hybrid, "THETARELAY_ESCALATE_ON_STOP": "0"}
    assert routing.ARMS["hybrid-no-triage"] == {**hybrid, "THETARELAY_ROUTE_MODE": "judge"}
    assert routing.ARMS["hybrid-no-size-limit"] == {
        **hybrid,
        "THETARELAY_LITE_MAX_REQUEST_CHARS": "1000000000",
    }
    assert routing.ARMS["triage"] == {"THETARELAY_ROUTE_MODE": "triage"}


def test_random_hybrid_ignores_ablation_logs(tmp_path: Path) -> None:
    _turns(tmp_path, "hybrid", 2, 3)
    (tmp_path / "turns-hybrid-no-stop-0.jsonl").write_text('{"tier": "lite"}\n' * 10)
    env = routing.arm_env("random-hybrid", tmp_path)
    assert env["THETARELAY_RANDOM_LITE_SHARE"] == "0.400"
