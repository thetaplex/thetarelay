# Design: fix failure detection

## Context

`events()` in `src/thetarelay/router.py` turns both API formats into a list of events; each
`result` event gets `failed` from `_FAILURE.search(text)` (or `is_error` for Anthropic). Tool
results are not linked to their calls.

## Decisions

- **Link by id while building events.** Keep a map from tool call id to tool name and arguments
  as `call` events are read; look it up for each `result`. Alternative: link by position
  (result follows call). Rejected: agents send parallel calls with results in any order.
- **"Executes code" = not a read tool.** `read`-group shell commands (`rg`, `cat`) are still shell
  commands and can report a non-zero exit, so the check uses the tool, not the step group: read
  *tools* are skipped, every shell result is checked. Exit code 1 from `rg`/`grep` (no match) is
  not a failure.
- **Test summary patterns:** pytest (`N failed`, `N passed`, `N error`), unittest
  (`FAILED (failures=N)`, `OK`), Jest/Vitest (`Tests: N failed`), cargo (`test result: FAILED`).
  The last summary line in the output wins.

## Risks / Trade-offs

- [Agents phrase exit status differently] → Support Pi's `Command exited with code N` and
  `exit code N` / `exit status N`; record unknown formats from turn logs.
- [Judge threshold was tuned on the old summary] → Re-run `scripts/judge_eval` after the change.
