# Design: fix failure detection

## Context

`events()` in `src/thetarelay/router.py` turns both API formats into a list of events. Each
`result` event gets `failed` from `_FAILURE.search(text)`, or from `is_error` for Anthropic.
Tool results are not linked to their calls. `failed` feeds the judge summary (`_step_line`,
`failed_results`) and `requests_since_failure`, which drives the stay rule.

## Decisions

- **Link by id while building events.** Keep a map from tool call id to tool name and arguments
  as `call` events are read, and look it up for each `result`. Alternative: link by position
  (the result follows the call). Rejected: agents send parallel calls, and their results can
  come in any order.
- **Use the step group of the call.** `classify(name, arguments)` from `step-classification`
  gives `read`, `run`, `write` or `irreversible`. For a `read` step only the error flag and the
  exit status count, so `cat` or `rg` output that mentions errors is not a failure. Alternative
  (the earlier draft): skip read tools only and check every shell result. Rejected: read-only
  shell commands then fall back to keywords and fail on file contents.
- **Signal order:** error flag, exit status, test summary, then keywords (`run`, `write` and
  `irreversible` steps only). The first signal present decides.
- **Exit status formats:** Pi `Command exited with code N`, plus `exit code N` and
  `exit status N`. Pi adds its line only when N is not 0, so a Pi result without one succeeded.
- **No-match exit:** exit code 1 from a `read` step whose last part is `rg`, `grep`, `egrep` or
  `fgrep` is not a failure, checked before the error flag: Claude Code may flag every non-zero
  exit as an error. With the flag set and no exit line, the step still fails. The command must be parsed again to find that part; an unlinked or
  unparsable call gets no exception.
- **Test summary patterns:** pytest (`N failed`, `N passed`, `N error`), unittest
  (`FAILED (failures=N)`, `OK`), Jest/Vitest (`Tests: N failed`), cargo (`test result: FAILED`).
  The last summary line in the output wins.
- **Whole lines only:** exit status and summary patterns must match a whole line (after
  stripping whitespace and pytest's `=` framing), so `processed 3 passed items` cannot hide a
  Traceback. A passing pytest summary needs its timing (`in 2.1s`); a failing one does not, since
  it can only add a failure. A bare `OK` counts only after a `Ran N tests` line.
- **Non-shell reads:** a `read` call with no `command` argument returns file content, so its
  text is never checked for exit lines.

## Risks / Trade-offs

- [Other agents phrase exit status differently] → Record unknown formats from turn logs; until
  then their results fall back to keywords, which is today's behaviour.
- [Fewer failures means more lite routing] → Run the routing eval again and check the judge
  threshold (follow-up); the old turn logs keep only summaries and cannot be re-scored.
