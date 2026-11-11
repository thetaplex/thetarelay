# Design: usage report

## Context

The turn log (`THETARELAY_TURN_LOG`) has one JSON line per request with `tier`, `model`,
`reason`, `input_tokens`, `cached_tokens`, `output_tokens`, `cost_usd` (OpenRouter's reported
cost; `null` for a local lite server), and `triage_cost_usd` on handed-back triage requests.
Change 03 adds `cache_write_tokens`; the report reads it when present.

## Decisions

- **Module:** `src/thetarelay/report.py` with pure functions (`load`, `summarise`, `format`) and
  a `thetarelay report PATH` subcommand in `cli.py`. No new dependency.
- **Actual cost:** the sum of `cost_usd` and `triage_cost_usd` over all lines. A missing or
  `null` cost counts as 0, and the report says how many requests had no reported cost. A tier
  whose lines all lack a cost shows `n/a`.
- **Frontier-only estimate:** frontier lines at their actual cost, plus each lite line priced at
  the frontier model's prices with the same token counts: `uncached * input price +
  cached * cache-read price + cache_write * cache-write price + output * output price`. Uncached is `input_tokens`
  for an Anthropic line (it already excludes cache reads and writes) and
  `max(0, input_tokens - cached - cache_write)` for an OpenAI-format line or one without
  `provider`. The frontier model is the most common `model` among frontier lines, or `--frontier-model`.
- **Prices:** from OpenRouter's public models list (`GET https://openrouter.ai/api/v1/models`,
  no key needed), field `pricing` (USD per token as strings: `prompt`, `completion`,
  `input_cache_read`, `input_cache_write`; a missing cache price falls back to `prompt`). With
  `--offline`, prices come only from `--price-input`, `--price-output`, `--price-cache-read` and
  `--price-cache-write` (USD per million tokens); without them the estimate is `n/a`. A failed
  price lookup also gives `n/a`, with the reason, and the rest of the report still prints.
- **Saving:** `1 - actual / estimate`, as a percentage; `n/a` when the estimate is `n/a` or 0.
- **Output:** a plain-text table on stdout; `--json` prints the same numbers as one JSON object.
  The estimate line says "estimate" and states the assumption in one line.
- **Bad lines:** lines that are not valid JSON objects are skipped and counted.

## Risks / Trade-offs

- [The frontier model would write a different number of output tokens] → Labelled as an
  estimate, with the assumption printed.
- [Prices change] → The report prints the prices it used and where they came from.
