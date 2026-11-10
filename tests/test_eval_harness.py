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
