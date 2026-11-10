# Proposal: fix failure detection

## Why

A failed step keeps the conversation on the frontier tier and is shown to the judge as
`result FAILED:`. Today any tool result that matches a keyword (`Exception`, `Error:`,
`Traceback`) counts as a failure, including file reads and search output. Reading a Python file
with `except Exception` marks the step as failed, which pushes the judge toward "hard" and would
trigger the stay rule if `THETARELAY_STAY_REQUESTS` were set.

## What Changes

- Link each tool result to its tool call (`tool_use_id` / `tool_call_id`) and its tool name.
- Check for failure only on results of shell commands and other tools that run code, not on
  `read`, `grep`, `glob` or `ls` results.
- Prefer structured signals over keywords: the result's `is_error` flag, the exit status the
  agent reports (Pi: `Command exited with code N`), and test runner summaries
  (`3 failed, 41 passed`). Keywords are used only when none of these is present.
- A test summary with zero failures is not a failure, even if the output contains `Error:`.

## Capabilities

### New Capabilities

- `failure-detection`: deciding whether an agent step failed.

### Modified Capabilities

None (no specs exist yet; current behaviour is undocumented in specs).

## Impact

- `src/thetarelay/router.py`: `events()` and the failure check; the judge summary changes for
  conversations that read files with error keywords.
- Judge input changes, so the judge threshold should be re-checked on `scripts/judge_eval`.
- Uses the read-tool list from `01-add-step-classification`, not the step groups.
