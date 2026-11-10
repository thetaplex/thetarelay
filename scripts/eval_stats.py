"""Confidence intervals shared by the evaluation reports (standard library only)."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence

MIN_RUNS = 3
RESAMPLES = 10_000


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval (lo, hi) for k successes in n trials."""
    p = k / n
    c = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    d = 1 + z * z / n
    return (p + z * z / (2 * n) - c) / d, (p + z * z / (2 * n) + c) / d


def newcombe(a: list[bool], b: list[bool], z: float = 1.96) -> tuple[float, float, float]:
    """Paired difference of proportions mean(a) - mean(b) with Newcombe's hybrid score CI."""
    n = len(a)
    pa, pb = sum(a) / n, sum(b) / n
    la, ua = wilson(sum(a), n, z)
    lb, ub = wilson(sum(b), n, z)
    n11 = sum(x and y for x, y in zip(a, b, strict=True))
    n10 = sum(x and not y for x, y in zip(a, b, strict=True))
    n01 = sum(y and not x for x, y in zip(a, b, strict=True))
    n00 = n - n11 - n10 - n01
    denom = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    phi = (n11 * n00 - n10 * n01) / denom if denom else 0.0
    diff = pa - pb
    lo = diff - math.sqrt((pa - la) ** 2 - 2 * phi * (pa - la) * (ub - pb) + (ub - pb) ** 2)
    hi = diff + math.sqrt((ua - pa) ** 2 - 2 * phi * (ua - pa) * (pb - lb) + (pb - lb) ** 2)
    return diff, lo, hi


def _percentile_ci(
    values: Sequence[float], point: float
) -> tuple[float, float | None, float | None]:
    """(point, lo, hi): 95% percentile bootstrap of the mean of values, seed 0."""
    if len(values) < MIN_RUNS:
        return point, None, None
    rng = random.Random(0)
    n = len(values)
    means = sorted(statistics.fmean(rng.choices(values, k=n)) for _ in range(RESAMPLES))
    return point, means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]


def bootstrap_mean(values: Sequence[float]) -> tuple[float, float | None, float | None]:
    """(mean, lo, hi); bounds are None with fewer than MIN_RUNS values."""
    return _percentile_ci(values, statistics.fmean(values))


def bootstrap_paired_diff(
    a: Sequence[float], b: Sequence[float]
) -> tuple[float, float | None, float | None]:
    """(mean(a - b), lo, hi) over pairs; bounds are None with fewer than MIN_RUNS pairs."""
    return bootstrap_mean([x - y for x, y in zip(a, b, strict=True)])
