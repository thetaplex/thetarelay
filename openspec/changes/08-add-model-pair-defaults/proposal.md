# Proposal: add model pair defaults

## Why

`docs/reference/tested-models.md` says OpenAI frontier models never hand steps to the lite
model, so the triage note only adds cost for them. Users who set an OpenAI frontier model keep
the `hybrid` default and pay for triage that cannot work.

## What Changes

- When `THETARELAY_ROUTE_MODE` is not set, the default depends on the frontier model: `judge`
  for `openai/` models, `hybrid` for all others.
- The proxy prints the chosen mode and why at start-up.

## Capabilities

### New Capabilities

- `model-pair-defaults`: route mode defaults chosen by model pair.

### Modified Capabilities

None.

## Impact

- `src/thetarelay/config.py`, start-up message, docs (configuration, tested models).
- Behaviour change for users of OpenAI frontier models who did not set a route mode; note it in
  the changelog.

## Decided

- Prefix rule (`openai/`), no table of exact names. See `design.md`.
- The tested-pairs row for Opus 5.5 with a local Qwen lite model needs a paid evaluation run and
  is not part of this change.
