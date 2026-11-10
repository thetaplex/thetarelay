"""Tests for `thetarelay report` (turn-log usage report)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from thetarelay import report
from thetarelay.cli import main

OPUS = "anthropic/claude-opus-5.5"
PRICES = {"input": 5.0, "output": 25.0}  # USD per million tokens


def line(
    tier: str = "lite",
    reason: str = "judge",
    inp: int = 0,
    cached: int = 0,
    out: int = 0,
    cost: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    model = OPUS if tier == "frontier" else "lite-model"
    return {
        "tier": tier, "model": model, "reason": reason, "input_tokens": inp,
        "cached_tokens": cached, "output_tokens": out, "cost_usd": cost, **extra,
    }  # fmt: skip


def write(tmp_path: Path, rows: list[dict[str, Any]], junk: int = 0) -> Path:
    p = tmp_path / "turns.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n" * junk)
    return p


def build(path: Path, **kw: Any) -> dict[str, Any]:
    kw.setdefault("offline", True)
    kw.setdefault("prices", PRICES)
    return report.build(path, **kw)


def test_estimate_arithmetic(tmp_path: Path) -> None:
    rows = [line(inp=1_000_000, out=10_000, cost=0.01), line("frontier", cost=1.0)]
    r = build(write(tmp_path, rows))
    assert r["estimate_usd"] == pytest.approx(5.25 + 1.0)


def test_null_cost_counts_zero_and_is_reported(tmp_path: Path) -> None:
    rows = [line(inp=10, out=1) for _ in range(60)]
    r = build(write(tmp_path, rows))
    assert r["actual_usd"] == 0
    assert r["no_cost_requests"] == 60
    assert "60 requests had no reported cost" in report.format_text(r)


def test_mixed_log_tiers_total_and_estimate(tmp_path: Path) -> None:
    rows = [line(inp=1000, out=100, cost=0.001) for _ in range(60)]
    rows += [line("frontier", inp=1000, out=100, cost=0.01) for _ in range(40)]
    r = build(write(tmp_path, rows))
    assert r["tiers"]["lite"]["requests"] == 60
    assert r["tiers"]["frontier"]["requests"] == 40
    assert r["actual_usd"] == pytest.approx(0.06 + 0.4)
    assert r["estimate_usd"] is not None
    text = report.format_text(r)
    assert "lite" in text
    assert "frontier" in text


def test_triage_cost_added_to_actual(tmp_path: Path) -> None:
    r = build(write(tmp_path, [line("frontier", cost=1.0, triage_cost_usd=0.5)]))
    assert r["actual_usd"] == pytest.approx(1.5)


def test_cache_prices_and_write_tokens(tmp_path: Path) -> None:
    rows = [line(inp=1_000_000, cached=400_000, out=0, cache_write_tokens=100_000)]
    prices = {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25}
    r = build(write(tmp_path, rows), prices=prices)
    # (1M - 0.4M) * 5 + 0.4M * 0.5 + 0.1M * 6.25
    assert r["estimate_usd"] == pytest.approx(3.0 + 0.2 + 0.625)
    assert r["tiers"]["lite"]["cache_write_tokens"] == 100_000


def test_label_says_estimate(tmp_path: Path) -> None:
    text = report.format_text(build(write(tmp_path, [line(inp=1, out=1)])))
    assert "estimate" in text.lower()


def test_fallbacks_by_reason(tmp_path: Path) -> None:
    rows = [line(reason="judge; lite_stopped") for _ in range(5)]
    rows += [line("frontier", reason="judge; lite_failed")]
    r = build(write(tmp_path, rows))
    assert r["reasons"]["lite_stopped"] == 5
    assert r["reasons"]["lite_failed"] == 1
    assert "lite_stopped: 5" in report.format_text(r)


def test_bad_lines_skipped_and_counted(tmp_path: Path) -> None:
    r = build(write(tmp_path, [line(inp=1, out=1)], junk=3))
    assert r["skipped_lines"] == 3
    assert r["tiers"]["lite"]["requests"] == 1


def test_offline_makes_no_network_request(tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network used")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    r = build(write(tmp_path, [line(inp=1_000_000)]), client=client)
    assert r["estimate_usd"] == pytest.approx(5.0)


def test_offline_without_prices_is_na(tmp_path: Path) -> None:
    r = report.build(write(tmp_path, [line(cost=0.5)]), offline=True, prices=None)
    assert r["estimate_usd"] is None
    assert r["actual_usd"] == 0.5
    text = report.format_text(r)
    assert "n/a" in text
    assert "0.5" in text


def test_price_lookup_from_openrouter(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        data = [{"id": OPUS, "pricing": {"prompt": "0.000005", "completion": "0.000025"}}]
        return httpx.Response(200, json={"data": data})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = [line(inp=1_000_000, out=10_000), line("frontier", cost=1.0)]
    r = report.build(write(tmp_path, rows), client=client)
    assert seen == ["https://openrouter.ai/api/v1/models"]
    assert r["estimate_usd"] == pytest.approx(6.25)  # most common frontier model


def test_price_lookup_failure_is_na_with_reason(tmp_path: Path) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    r = report.build(write(tmp_path, [line(cost=0.2)]), client=client)
    assert r["estimate_usd"] is None
    assert r["price_error"]
    text = report.format_text(r)
    assert "n/a" in text
    assert r["price_error"] in text


def test_json_output_via_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("THETARELAY_JUDGE_URL", raising=False)  # report needs no proxy settings
    path = write(tmp_path, [line(inp=1_000_000, out=10_000, cost=0.01)])
    main(["report", str(path), "--offline", "--price-input", "5", "--price-output", "25", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["estimate_usd"] == pytest.approx(5.25)
    assert out["actual_usd"] == pytest.approx(0.01)


def test_cached_above_input_is_clamped(tmp_path: Path) -> None:
    r = build(
        write(tmp_path, [line(inp=100, cached=1_000_000)]), prices={"input": 5.0, "output": 25.0}
    )
    assert r["estimate_usd"] >= 0


def test_text_assumption_line_and_singular_counts(tmp_path: Path) -> None:
    rows = [{"tier": None, "reason": "", "input_tokens": 1}, line("frontier", cost=1.0)]
    path = write(tmp_path, rows, junk=1)
    prices = {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25}
    text = report.format_text(build(path, prices=prices, frontier_model=OPUS))
    assert (
        f"Estimate: lite requests priced at {OPUS} prices with the same token counts "
        "(input $5.00/M, output $25.00/M, cache read $0.50/M, cache write $6.25/M; "
        "source: given)"
    ) in text
    assert "1 line skipped" in text
    assert "1 request had no reported cost" in text
    assert "unknown" in text
    assert "None" not in text
    assert "{" not in text
