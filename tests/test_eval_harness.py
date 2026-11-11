import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

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


def _attempts(arm: str, runs: int, passes: list[bool]) -> list[dict[str, Any]]:
    return [
        {"arm": arm, "run": r, "exercise": f"ex{i}", "passed": p, "agent_s": 1.0}
        for r in range(runs)
        for i, p in enumerate(passes)
    ]


def _tlog(runs: int, lite: int, frontier: int, cost: float) -> list[dict[str, Any]]:
    rows = []
    for r in range(runs):
        rows += [{"run": r, "tier": "lite", "cost_usd": 0.0}] * lite
        rows += [{"run": r, "tier": "frontier", "cost_usd": cost + r}] * frontier
    return rows


def test_report_paired_difference_with_intervals() -> None:
    passes = [True, True, False, True]
    att = _attempts("hybrid", 10, passes) + _attempts(
        "random-hybrid", 10, [True, False, False, True]
    )
    turns = {"hybrid": _tlog(10, 1, 3, 1.0), "random-hybrid": _tlog(10, 1, 3, 2.0)}
    out = routing.build_report(att, turns)
    assert "too few runs" not in out
    line = next(ln for ln in out.splitlines() if ln.startswith("hybrid vs random-hybrid"))
    assert "pass +" in line
    assert line.count("[") == 2  # pass-rate and cost intervals
    row = next(ln for ln in out.splitlines() if ln.startswith("hybrid "))
    assert " 10 " in row
    assert "25%" in row  # lite share


def test_report_too_few_runs_has_no_interval() -> None:
    att = _attempts("hybrid", 2, [True, False]) + _attempts("random-hybrid", 2, [True, True])
    turns = {"hybrid": _tlog(2, 1, 1, 1.0), "random-hybrid": _tlog(2, 1, 1, 1.0)}
    out = routing.build_report(att, turns)
    rows = [ln for ln in out.splitlines() if ln.startswith(("hybrid", "random-hybrid"))]
    assert rows
    for ln in rows:
        assert "too few runs" in ln
        assert "[" not in ln


def test_report_counts_unpaired_and_handles_missing_turns() -> None:
    att = _attempts("hybrid", 3, [True, False]) + _attempts("random-hybrid", 3, [True])
    out = routing.build_report(att, {})
    assert "unpaired" in out


def test_report_cost_includes_handed_back_triage_calls() -> None:
    att = _attempts("hybrid", 1, [True, True])
    rows = [
        {"run": 0, "tier": "frontier", "cost_usd": 0.5},
        {"run": 0, "tier": "lite", "cost_usd": 0.1, "triage_cost_usd": 0.4},
    ]
    row = next(
        ln
        for ln in routing.build_report(att, {"hybrid": rows}).splitlines()
        if ln.startswith("hybrid ")
    )
    assert "$0.500" in row  # (0.5 + 0.1 + 0.4) / 2 tasks


def _load_app() -> ModuleType:
    spec = importlib.util.spec_from_file_location("app_eval_run", SCRIPTS / "app_eval" / "run.py")
    assert spec
    assert spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["app_eval_run"] = mod
    spec.loader.exec_module(mod)
    return mod


app = _load_app()


def _specs(root: Path, *names: str) -> None:
    for n in names:
        (root / n / "starter").mkdir(parents=True)
        (root / n / "starter" / "SPEC.md").write_text(n)
        (root / n / "hidden_tests").mkdir()
        (root / n / "hidden_tests" / "test_hidden.py").write_text("")


def test_app_ships_several_specs() -> None:
    names = [p.name for p in app.select_specs("")]
    assert len(names) >= 3
    assert {"bookmarks", "todo-cli", "inventory"} <= set(names)
    for p in app.select_specs(""):
        assert (p / "starter" / "SPEC.md").is_file()
        assert any((p / "hidden_tests").glob("test_*.py"))


def test_specs_filter(tmp_path: Path) -> None:
    _specs(tmp_path, "a", "b", "c")
    assert [p.name for p in app.select_specs("", tmp_path)] == ["a", "b", "c"]
    assert [p.name for p in app.select_specs("c,a", tmp_path)] == ["a", "c"]
    with pytest.raises(SystemExit):
        app.select_specs("nope", tmp_path)


def test_run_builds_every_spec_for_every_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs, results = tmp_path / "specs", tmp_path / "results"
    _specs(specs, "a", "b")
    monkeypatch.setattr(app, "SPECS", specs)
    monkeypatch.setattr(app, "RESULTS", results)
    seen: list[tuple[str, dict[str, str] | None]] = []

    def fake_claude(work: Path, out: Path) -> dict[str, Any]:
        seen.append(((work / "SPEC.md").read_text(), None))
        return {"status": "ok", "seconds": 1.0, "api_cost_usd": 1.0}

    def fake_pi(work: Path, out: Path, env: dict[str, str], turn_log: Path) -> dict[str, Any]:
        seen.append(((work / "SPEC.md").read_text(), env))
        assert turn_log.parent == results / (work / "SPEC.md").read_text()
        return {"status": "ok", "seconds": 1.0, "api_cost_usd": 0.5}

    def fake_grade(work: Path, spec: Path) -> dict[str, Any]:
        assert spec.name == (work / "SPEC.md").read_text()
        return {"hidden_passed": 3, "hidden_failed": 0}

    monkeypatch.setattr(app, "run_claude", fake_claude)
    monkeypatch.setattr(app, "run_pi", fake_pi)
    monkeypatch.setattr(app, "grade", fake_grade)
    args = argparse.Namespace(runs=1, arms="claude,pi", specs="")
    app.run(args)
    rows = [json.loads(ln) for ln in (results / "builds.jsonl").read_text().splitlines()]
    assert sorted((r["spec"], r["arm"], r["run"]) for r in rows) == [
        ("a", "claude", 0),
        ("a", "pi-hybrid", 0),
        ("b", "claude", 0),
        ("b", "pi-hybrid", 0),
    ]
    assert ("a", {"THETARELAY_ROUTE_MODE": "hybrid"}) in seen
    app.run(args)  # everything done: nothing rebuilt
    assert len(seen) == 4


def test_resumed_build_drops_stale_turn_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs, results = tmp_path / "specs", tmp_path / "results"
    _specs(specs, "a")
    (results / "a").mkdir(parents=True)
    stale = results / "a" / "turns-pi-hybrid-0.jsonl"
    stale.write_text('{"stale": 1}\n')
    monkeypatch.setattr(app, "SPECS", specs)
    monkeypatch.setattr(app, "RESULTS", results)

    def fake_pi(work: Path, out: Path, env: dict[str, str], turn_log: Path) -> dict[str, Any]:
        assert not turn_log.exists()
        return {"status": "ok", "seconds": 1.0}

    monkeypatch.setattr(app, "run_pi", fake_pi)
    monkeypatch.setattr(app, "grade", lambda *_: {"hidden_passed": 1, "hidden_failed": 0})
    app.run(argparse.Namespace(runs=1, arms="pi", specs=""))


def test_app_arm_env(tmp_path: Path) -> None:
    assert app.arm_env("pi", tmp_path) == {"THETARELAY_ROUTE_MODE": "hybrid"}
    assert app.arm_env("pi-hybrid", tmp_path) == app.arm_env("pi", tmp_path)
    assert app.arm_env("pi-judge", tmp_path) == {"THETARELAY_ROUTE_MODE": "judge"}
    assert app.arm_env("pi-hybrid-no-triage", tmp_path) == routing.ARMS["hybrid-no-triage"]
    assert app.arm_env("pi-random-hybrid", tmp_path) is None
    _turns(tmp_path, "pi-hybrid", 1, 3)
    assert app.arm_env("pi-random-hybrid", tmp_path) == {
        "THETARELAY_ROUTE_MODE": "random",
        "THETARELAY_RANDOM_LITE_SHARE": "0.250",
    }
    with pytest.raises(SystemExit):
        app.arm_env("pi-nope", tmp_path)


def test_app_report_pairs_by_spec_and_run() -> None:
    rows = [
        {
            "spec": "bookmarks",
            "arm": "pi",
            "run": 0,
            "hidden_passed": 34,
            "hidden_failed": 0,
            "api_cost_usd": 2.0,
        },
        {"spec": "todo", "arm": "pi-hybrid", "run": 0, "hidden_passed": 5, "hidden_failed": 1},
        {
            "spec": "bookmarks",
            "arm": "pi-random-hybrid",
            "run": 0,
            "hidden_passed": 34,
            "hidden_failed": 0,
        },
        {
            "spec": "todo",
            "arm": "claude",
            "run": 0,
            "hidden_passed": 6,
            "hidden_failed": 0,
            "api_cost_usd": 3.0,
        },
    ]
    turns = {"pi-hybrid": [{"run": 0, "tier": "lite", "cost_usd": 0.0}]}
    out = app.build_report(rows, turns)
    assert "pi-hybrid vs pi-random-hybrid" in out
    assert "(1 pairs, 1 unpaired)" in out
    claude = next(ln for ln in out.splitlines() if ln.startswith("claude"))
    assert "$3.000" in claude


def _fake_builds(monkeypatch: pytest.MonkeyPatch, specs: Path, results: Path) -> list[str]:
    monkeypatch.setattr(app, "SPECS", specs)
    monkeypatch.setattr(app, "RESULTS", results)
    built: list[str] = []

    def fake_build(work: Path, out: Path, *_: Any) -> dict[str, Any]:
        built.append(out.name)
        return {"status": "ok", "seconds": 1.0, "api_cost_usd": 7.0}

    monkeypatch.setattr(app, "run_claude", fake_build)
    monkeypatch.setattr(app, "run_pi", fake_build)
    monkeypatch.setattr(app, "grade", lambda *_: {"hidden_passed": 1, "hidden_failed": 0})
    return built


def test_old_rows_without_spec_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    specs, results = tmp_path / "specs", tmp_path / "results"
    _specs(specs, "bookmarks")
    built = _fake_builds(monkeypatch, specs, results)
    results.mkdir()
    old = {"arm": "pi", "run": 0, "hidden_passed": 34, "hidden_failed": 0, "api_cost_usd": 99.0}
    (results / "builds.jsonl").write_text(json.dumps(old) + "\n")
    app.run(argparse.Namespace(runs=1, arms="pi-hybrid", specs=""))
    assert built == ["pi-hybrid-0"]
    assert "ignoring 1 old" in capsys.readouterr().out
    app.report(argparse.Namespace())
    out = capsys.readouterr().out
    assert "ignoring 1 old" in out
    assert "99" not in out
    assert "$7.000" in next(ln for ln in out.splitlines() if ln.startswith("pi-hybrid"))


def test_unknown_arm_fails_before_any_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs, results = tmp_path / "specs", tmp_path / "results"
    _specs(specs, "a")
    built = _fake_builds(monkeypatch, specs, results)
    with pytest.raises(SystemExit, match="pi-hybird"):
        app.run(argparse.Namespace(runs=1, arms="claude,pi-hybird", specs=""))
    assert built == []
