"""Usage report: what routing cost vs. the same requests on the frontier tier alone."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

MODELS_URL = "https://openrouter.ai/api/v1/models"
_FIELDS = ("input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens")


def load(path: str | Path) -> tuple[list[dict[str, Any]], int]:
    """Return (rows, skipped): lines that are not JSON objects are skipped and counted."""
    rows: list[dict[str, Any]] = []
    skipped = 0
    for raw in Path(path).read_text().splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            row = None
        if isinstance(row, dict):
            rows.append(row)
        else:
            skipped += 1
    return rows, skipped


def fetch_prices(model: str, client: httpx.Client) -> dict[str, float]:
    """Prices in USD per million tokens from OpenRouter's public models list."""
    resp = client.get(MODELS_URL)
    resp.raise_for_status()
    for entry in resp.json().get("data", []):
        if entry.get("id") == model:
            p = entry["pricing"]
            inp = float(p["prompt"]) * 1e6
            read, write = p.get("input_cache_read"), p.get("input_cache_write")
            return {
                "input": inp,
                "output": float(p["completion"]) * 1e6,
                "cache_read": float(read) * 1e6 if read else inp,
                "cache_write": float(write) * 1e6 if write else inp,
            }  # fmt: skip
    raise LookupError(f"model {model} not in OpenRouter models list")


def _tokens(row: dict[str, Any], key: str) -> int:
    v = row.get(key)
    return v if isinstance(v, int) else 0


def _line_estimate(row: dict[str, Any], p: dict[str, float]) -> float:
    inp = p["input"]
    cached = _tokens(row, "cached_tokens")
    write = _tokens(row, "cache_write_tokens")
    uncached = _tokens(row, "input_tokens")
    if row.get("provider") != "anthropic":  # OpenAI format: prompt_tokens includes cache
        uncached = max(0, uncached - cached - write)
    return (
        uncached * inp
        + cached * p.get("cache_read", inp)
        + _tokens(row, "cache_write_tokens") * p.get("cache_write", inp)
        + _tokens(row, "output_tokens") * p["output"]
    ) / 1e6


def _cost(row: dict[str, Any]) -> float | None:
    parts = [row.get("cost_usd"), row.get("triage_cost_usd")]
    nums = [x for x in parts if isinstance(x, int | float)]
    return sum(nums) if nums else None


def build(
    path: str | Path,
    *,
    frontier_model: str | None = None,
    offline: bool = False,
    prices: dict[str, float] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    rows, skipped = load(path)
    tiers: dict[str, dict[str, Any]] = {}
    reasons: Counter[str] = Counter()
    no_cost = 0
    for row in rows:
        t = tiers.setdefault(
            str(row.get("tier") or "unknown"),
            {"requests": 0, **dict.fromkeys(_FIELDS, 0), "cost_usd": None},
        )
        t["requests"] += 1
        for f in _FIELDS:
            t[f] += _tokens(row, f)
        c = _cost(row)
        if c is None:
            no_cost += 1
        else:
            t["cost_usd"] = (t["cost_usd"] or 0) + c
        reasons.update((str(row.get("reason") or "unknown")).split("; "))
    actual = sum(t["cost_usd"] or 0 for t in tiers.values())

    models = Counter(r.get("model") for r in rows if r.get("tier") == "frontier")
    model = frontier_model or (models.most_common(1)[0][0] if models else None)
    price_error = None
    source = "given" if prices is not None else None
    if prices is None and not offline:
        try:
            if not model:
                raise LookupError("no frontier model: no frontier lines and no --frontier-model")
            if client is None:
                with httpx.Client(timeout=15) as http:
                    prices = fetch_prices(model, http)
            else:
                prices = fetch_prices(model, client)
        except (httpx.HTTPError, LookupError, KeyError, ValueError, AttributeError) as e:
            price_error = str(e) or type(e).__name__
        else:
            source = "OpenRouter"
    estimate = None
    if prices is not None:
        estimate = sum(
            (_cost(r) or 0) if r.get("tier") == "frontier" else _line_estimate(r, prices)
            for r in rows
        )
    saving = 1 - actual / estimate if estimate else None
    return {
        "tiers": tiers,
        "actual_usd": actual,
        "estimate_usd": estimate,
        "saving_pct": None if saving is None else saving * 100,
        "no_cost_requests": no_cost,
        "reasons": dict(reasons),
        "skipped_lines": skipped,
        "frontier_model": model,
        "prices_usd_per_million": prices,
        "prices_source": source,
        "price_error": price_error,
    }


def _usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.4f}"


def format_text(r: dict[str, Any]) -> str:
    out = ["tier      requests     input    cached  cache_wr    output  cost"]
    for name, t in sorted(r["tiers"].items()):
        out.append(
            f"{name:<9}{t['requests']:>9}{t['input_tokens']:>10}{t['cached_tokens']:>10}"
            f"{t['cache_write_tokens']:>10}{t['output_tokens']:>10}  {_usd(t['cost_usd'])}"
        )
    out.append(f"actual cost: {_usd(r['actual_usd'])}")
    est = _usd(r["estimate_usd"])
    if r["price_error"]:
        est += f" ({r['price_error']})"
    out.append(f"frontier-only cost (estimate): {est}")
    pr = r["prices_usd_per_million"]
    model = f"{r['frontier_model']} " if r["frontier_model"] else "frontier "
    line = f"Estimate: lite requests priced at {model}prices with the same token counts"
    if pr:
        inp = pr["input"]
        line += (
            f" (input ${inp:.2f}/M, output ${pr['output']:.2f}/M, "
            f"cache read ${pr.get('cache_read', inp):.2f}/M, "
            f"cache write ${pr.get('cache_write', inp):.2f}/M; source: {r['prices_source']})"
        )
    out.append(line)
    out.append(
        "saving: n/a" if r["saving_pct"] is None else f"saving: {r['saving_pct']:.1f}% (estimate)"
    )
    if r["no_cost_requests"]:
        n = r["no_cost_requests"]
        out.append(f"{n} request{'s' * (n != 1)} had no reported cost (counted as 0)")
    if r["skipped_lines"]:
        n = r["skipped_lines"]
        out.append(f"{n} line{'s' * (n != 1)} skipped (not valid JSON)")
    if r["reasons"]:
        out.append("reasons:")
        out += [f"  {k}: {v}" for k, v in sorted(r["reasons"].items())]
    return "\n".join(out)
