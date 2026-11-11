# Proposal: fix failure detection

## Why

A failed step keeps the conversation on the frontier tier. It is shown to the judge as
`result FAILED:`, counted in `failed_results`, and starts the stay rule
(`THETARELAY_STAY_REQUESTS`). Today a tool result counts as failed when it matches a keyword
(`Exception`, `Error:`, `Traceback`). This goes wrong in two ways:

- **False failures.** Reading code or docs that mention errors counts as a failure. In the routing
  eval turn logs, about 12% of `read` results are marked `FAILED`, for example
  `class EmptyListException(Exception):` and `## Customizing and Raising Exceptions`. Read-only
  shell commands such as `cat .docs/*` fail the same way.
- **Missed failures.** Pi reports a failed command as `Command exited with code N`, and only when
  N is not 0. The keyword pattern matches `exit code N` but not this line, so a failed command
  counts only if its output also happens to contain a keyword.

## What Changes

- Link each tool result to its tool call (`tool_use_id` / `tool_call_id`), and classify the call
  with the step groups of `step-classification`.
- Decide failure from structured signals first: the result's `is_error` flag, a reported exit
  status (including Pi's `Command exited with code N`), then a test runner summary
  (`3 failed, 41 passed`).
- Use keywords only for `run`, `write` and `irreversible` steps that have no structured signal.
  `read` steps never fail on keywords.
- Exit code 1 from a `read` search command (`rg`, `grep`) means "no match", not failure.
- A test summary with zero failures is not a failure, even if the output contains `Error:`.

## Capabilities

### New Capabilities

- `failure-detection`: deciding whether an agent step failed.

### Modified Capabilities

None.

## Impact

- `src/thetarelay/router.py`: `events()` and the failure check. Judge summaries change for
  conversations that read files with error keywords, and for Pi commands that exit non-zero
  without a keyword.
- Judge input changes. The routing eval turn logs keep only summaries, so they cannot be
  re-scored; `scripts/failure_eval.py` compares old and new verdicts on captured app eval runs.
  Checking the judge threshold needs a new routing eval run and is a follow-up.
  `scripts/judge_eval` uses hand-written summaries and does not go through `events()`.
- Uses `classify` from `step-classification`.
