# Tasks

Spec: `specs/failure-detection/spec.md`. Design: `design.md`. All code in
`src/thetarelay/router.py`; tests in `tests/test_router.py`. `just check` must pass after each
task. Tests never call real APIs.

## Task 1: Link results to their calls

- [ ] 1.1 Write failing tests for the "Parallel calls" and "Unlinked result" scenarios, in both
  Anthropic (`tool_use.id` / `tool_result.tool_use_id`) and OpenAI (`tool_calls[].id` /
  `tool_call_id`) formats; verify they fail with `just test tests/test_router.py`
- [ ] 1.2 In `events()`, keep a map from call id to `(name, arguments)` while reading calls, and
  give each `result` Event two new fields: `group` (from `thetarelay.steps.classify`;
  `"irreversible"` when the id matches no call) and `command` (the call's `command` argument
  when it is a string, else `None`). Non-result events keep `group=None`, `command=None`.
  Failure detection is unchanged in this task. Verify the 1.1 tests pass

## Task 2: Decide failure from structured signals

- [ ] 2.1 Write failing tests for every scenario under "Structured signals take precedence",
  "Read steps do not fail on content" and "Failure line shown to the judge"; verify they fail
- [ ] 2.2 Replace the `_FAILURE.search` checks with one function that returns the deciding line
  or `None`, applying, in order: the error flag (`is_error`); a reported exit status
  (`Command exited with code N`, `exit code N`, `exit status N`; N = 0 is success); a test
  summary (pytest, unittest, Jest/Vitest, cargo, per `design.md`; the last summary line wins);
  then keywords, only when the result's group is not `read`. Exit code 1 is not a failure for a
  `read` step whose last shell part is `rg`, `grep`, `egrep` or `fgrep`. Remove the exit-status
  alternatives from the keyword pattern. Store the deciding line on the Event and use it in
  `_step_line` for `result FAILED: …`. Verify the 2.1 tests and the existing tests pass

## Task 3: Measure and wrap up

- [ ] 3.1 Add `scripts/failure_eval.py`: for every captured `scripts/app_eval/results*/**/bodies.jsonl`,
  count unique tool results by step group and by failed/not failed under the old keyword check
  (the `_FAILURE` regex as of `main`, copied into the script) and the new check, and print both
  tables plus every result whose verdict changed (tool, first 80 characters of the deciding
  line). Run it and paste the output into the task report
- [ ] 3.2 Add a `CHANGELOG.md` entry under Unreleased (Fixed): reading files that mention errors
  no longer counts as a failed step, and Pi commands that exit non-zero are now caught
- [ ] 3.3 Run `openspec validate 02-fix-failure-detection --strict` and `just check`; verify both
  pass
