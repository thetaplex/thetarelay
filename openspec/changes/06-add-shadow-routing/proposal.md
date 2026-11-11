# Proposal: add shadow routing

## Why

The judge's labelled set is 59 hand-written steps, and the judge is close to chance on real
turns. Triage verdicts (see `03-log-routing-signals`) add labels only for steps the judge was
unsure about; steps it confidently sent to the lite tier are never checked. A safe way to label
real steps is to run the lite model next to the frontier, without using its answer.

## What Changes

- New setting `THETARELAY_SHADOW_SHARE` (0.0 to 1.0, default 0.0): the share of frontier-tier
  requests that are also sent to the lite tier in the background. The lite reply is never
  returned to the agent.
- While the share is above 0 and a turn log is set, the judge is asked on every request in
  every route mode, and its answer is logged, so `frontier` mode collects judge decisions too.
- The turn log records the shadow lite reply's tool calls, whether they match the frontier's
  (same tool, same target: file path or shell command), and the shadow's cost.

## Capabilities

### New Capabilities

- `shadow-routing`: background lite runs on frontier steps, for labelling.

### Modified Capabilities

None. The `shadow` turn log field is specified in `shadow-routing`.

## Impact

- `src/thetarelay/proxy.py`, `src/thetarelay/config.py`, docs.
- Extra load on the lite server; if the lite tier is hosted, extra cost per shadow request.
- Depends on `01-add-step-classification` and `03-log-routing-signals`.

## Decided

- Match rule: `match` compares tool and target only. Both replies' calls are logged with a hash
  of their arguments (no edit text), so stricter rules can be applied offline. See `design.md`.
- The shadow result goes on the request's own turn log line, written after the shadow finishes.
