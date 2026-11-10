"""Tests for `thetarelay report` (turn-log usage report)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from thetarelay import report
from thetarelay.cli import main

OPUS = "anthropic/claude-opus-5.5"
PRICES = {"input": 5.0, "output": 25.0}  # USD per million tokens


def line(tier="lite", reason="judge", inp=0, cached=0, out=0, cost=None, **extra) -> dict:
    model = OPUS if tier == "frontier" else "lite-model"
    return {
        "tier": tier, "model": model, "reason": reason, "input_tokens": inp,
        "cached_tokens": cached, "output_tokens": out, "cost_usd": cost, **extra,
    }  # fmt: skip


def write(tmp_path: Path, rows: list, junk: int = 0) -> Path:
    p = tmp_path / "turns.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n" * junk)
    return p


def build(path, **kw):
    kw.setdefault("offline", True)
    kw.setdefault("prices", PRICES)
    return report.build(path, **kw)


def test_estimate_arithmetic(tmp_path):
    rows = [line(inp=1_000_000, out=10_000, cost=0.01), line("frontier", cost=1.0)]
    r = build(write(tmp_path, rows))
    assert r["estimate_usd"] == pytest.approx(5.25 + 1.0)


def test_null_cost_counts_zero_and_is_reported(tmp_path):
    rows = [line(inp=10, out=1) for _ in range(60)]
    r = build(write(tmp_path, rows))
    assert r["actual_usd"] == 0
    assert r["no_cost_requests"] == 60
    assert "60 requests had no reported cost" in report.format_text(r)


def test_mixed_log_tiers_total_and_estimate(tmp_path):
    rows = [line(inp=1000, out=100, cost=0.001) for _ in range(60)]
    rows += [line("frontier", inp=1000, out=100, cost=0.01) for _ in range(40)]
    r = build(write(tmp_path, rows))
    assert r["tiers"]["lite"]["requests"] == 60
    assert r["tiers"]["frontier"]["requests"] == 40
    assert r["actual_usd"] == pytest.approx(0.06 + 0.4)
    assert r["estimate_usd"] is not None
    text = report.format_text(r)
    assert "lite" in text and "frontier" in text


def test_triage_cost_added_to_actual(tmp_path):
    r = build(write(tmp_path, [line("frontier", cost=1.0, triage_cost_usd=0.5)]))
    assert r["actual_usd"] == pytest.approx(1.5)


def test_cache_prices_and_write_tokens(tmp_path):
    rows = [line(inp=1_000_000, cached=400_000, out=0, cache_write_tokens=100_000)]
    prices = {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25}
    r = build(write(tmp_path, rows), prices=prices)
    # (1M - 0.4M) * 5 + 0.4M * 0.5 + 0.1M * 6.25
    assert r["estimate_usd"] == pytest.approx(3.0 + 0.2 + 0.625)
    assert r["tiers"]["lite"]["cache_write_tokens"] == 100_000


def test_label_says_estimate(tmp_path):
    text = report.format_text(build(write(tmp_path, [line(inp=1, out=1)])))
    assert "estimate" in text.lower()


def test_fallbacks_by_reason(tmp_path):
    rows = [line(reason="judge; lite_stopped") for _ in range(5)]
    rows += [line("frontier", reason="judge; lite_failed")]
    r = build(write(tmp_path, rows))
    assert r["reasons"]["lite_stopped"] == 5
    assert r["reasons"]["lite_failed"] == 1
    assert "lite_stopped: 5" in report.format_text(r)


def test_bad_lines_skipped_and_counted(tmp_path):
    r = build(write(tmp_path, [line(inp=1, out=1)], junk=3))
    assert r["skipped_lines"] == 3
    assert r["tiers"]["lite"]["requests"] == 1


def test_offline_makes_no_network_request(tmp_path):
    def boom(request):
        raise AssertionError("network used")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    r = build(write(tmp_path, [line(inp=1_000_000)]), client=client)
    assert r["estimate_usd"] == pytest.approx(5.0)


def test_offline_without_prices_is_na(tmp_path):
    r = report.build(write(tmp_path, [line(cost=0.5)]), offline=True, prices=None)
    assert r["estimate_usd"] is None
    assert r["actual_usd"] == 0.5
    text = report.format_text(r)
    assert "n/a" in text and "0.5" in text


def test_price_lookup_from_openrouter(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        data = [{"id": OPUS, "pricing": {"prompt": "0.000005", "completion": "0.000025"}}]
        return httpx.Response(200, json={"data": data})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = [line(inp=1_000_000, out=10_000), line("frontier", cost=1.0)]
    r = report.build(write(tmp_path, rows), client=client)
    assert seen == ["https://openrouter.ai/api/v1/models"]
    assert r["estimate_usd"] == pytest.approx(6.25)  # most common frontier model


def test_price_lookup_failure_is_na_with_reason(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    r = report.build(write(tmp_path, [line(cost=0.2)]), client=client)
    assert r["estimate_usd"] is None
    assert r["price_error"]
    text = report.format_text(r)
    assert "n/a" in text and r["price_error"] in text


def test_json_output_via_cli(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("THETARELAY_JUDGE_URL", raising=False)  # report needs no proxy settings
    path = write(tmp_path, [line(inp=1_000_000, out=10_000, cost=0.01)])
    main(["report", str(path), "--offline", "--price-input", "5", "--price-output", "25", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["estimate_usd"] == pytest.approx(5.25)
    assert out["actual_usd"] == pytest.approx(0.01)
