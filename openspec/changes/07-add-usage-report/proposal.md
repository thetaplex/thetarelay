# Proposal: add usage report

## Why

Users have to trust the README's saving figure. They trust their own numbers more, and the turn
log already holds what is needed to compute them.

## What Changes

- New command `thetarelay report <turns.jsonl>`: requests and cost per tier, actual total cost,
  estimated frontier-only cost, cache hit rate per tier, and fallbacks by reason.
- The frontier-only estimate prices the lite tier's tokens at the frontier model's prices, read
  from OpenRouter's models API (`--offline` uses only prices given on the command line).

## Capabilities

### New Capabilities

- `usage-report`: the `thetarelay report` command.

### Modified Capabilities

None.

## Impact

- `src/thetarelay/cli.py` and a small report module; docs (CLI reference, a how-to).
- Reads `cache_write_tokens` from `03-log-routing-signals` when present.

## Out of scope

- Budget cap per day or per session (from the review). It needs a decision on what happens at
  the cap (block requests, or force the lite tier), and no user has asked for it.

## Decided

- The frontier-only estimate assumes the frontier model would have used the same token counts.
  The report labels it as an estimate and prints the assumption and the prices it used. See
  `design.md`.
