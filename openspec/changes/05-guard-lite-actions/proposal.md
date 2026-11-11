# Proposal: guard lite actions

## Why

The judge decides before the lite model acts and cannot see what it will do. Apart from
`THETARELAY_ESCALATE_ON_STOP`, nothing checks the lite reply. The lite model can push, publish,
or call a tool that does not exist, and the agent runs it.

## What Changes

- New setting `THETARELAY_LITE_GUARD`: which lite actions are discarded and re-sent to the
  frontier tier. Values: `off` (default), `irreversible`, `writes`.
  - `irreversible`: discard replies with an `irreversible` step or a malformed tool call.
  - `writes`: also discard replies with a `write` step, so the lite tier only reads and runs
    tests.
- A malformed tool call is one that names a tool the request did not offer, has arguments that
  are not valid JSON, or misses an argument the tool's schema marks required.
- Re-sent requests are marked in the reason header and turn log (`lite_irreversible`,
  `lite_wrote`, `lite_malformed`).
- Reuses the lite-reply buffering that `escalate_on_stop` already does.

## Capabilities

### New Capabilities

- `lite-action-guard`: checks on the lite reply before it reaches the agent.

### Modified Capabilities

None.

## Impact

- `src/thetarelay/proxy.py` (lite reply buffering), `src/thetarelay/config.py`.
- Docs: configuration, HTTP API reason values, how routing works.
- Depends on `01-add-step-classification`.

## Decided

- **Edit size rule: left out.** Size does not track risk: subtle bugs are usually one line, and
  long new files are often boilerplate. Choose an edit policy after the research protocol
  measures lite edit accuracy.
- Default stays `off` until an evaluation run shows the guard does not cost more than it saves.
- Level `writes` does not discard `run` steps: tests are the lite tier's main feedback and stay
  in the working tree. See `design.md`.
