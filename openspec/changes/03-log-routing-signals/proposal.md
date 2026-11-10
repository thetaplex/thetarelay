# Proposal: log routing signals

## Why

Three questions cannot be answered from today's turn log:

- What share of steps are reads, writes and irreversible actions (research protocol, phase 0)?
- Do lite streaks let the frontier prompt cache expire, so the next frontier call pays to write
  the whole conversation to the cache again? The log has cache reads but not cache writes.
- Which triaged steps did the frontier hand back? This can be inferred today (reason
  `judge; triage` plus the final tier), but not read directly, so building a label set from
  triage needs guesswork.

## What Changes

- Turn log: `step_groups` (the group of each tool call in the reply), `cache_write_tokens`
  (Anthropic `cache_creation_input_tokens`; OpenAI-format replies from OpenRouter where
  reported), and `triage_verdict` (`easy` if the frontier handed the step back, `did_step` if it
  answered itself; absent if the request was not triaged).
- No routing change.

## Capabilities

### New Capabilities

- `turn-log`: the JSON Lines record written for each proxied request. Existing fields are
  documented in `docs/reference/http-api.md`; this change adds the spec and the three fields.

### Modified Capabilities

None.

## Impact

- `src/thetarelay/proxy.py` (usage extraction, record), `docs/reference/http-api.md`.
- Step groups need the reply's tool calls, so streamed replies on the frontier tier are parsed
  buffered as they pass and parsed when the stream ends, with no delay to any chunk.
- Depends on `01-add-step-classification`.

## Out of scope

- Fixing cache expiry (longer cache lifetime, keep-alive requests). Both conflict with the
  forwarding rule in `AGENTS.md` and need a separate decision once the cache-write numbers show
  how often expiry happens.
