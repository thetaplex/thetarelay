# Tasks

Spec: `specs/turn-log/spec.md`. Code in `src/thetarelay/proxy.py`; tests in
`tests/test_router.py` (proxy tests live there, using `httpx.MockTransport`). Field docs in
`docs/reference/http-api.md` (Turn log table). `just check` must pass after each task.

## Task 1: Cache writes and triage verdict

- [x] 1.1 Write failing tests for every scenario under "Cache writes" and "Triage verdict", for
  streamed and non-streamed replies; verify they fail with `just test tests/test_router.py`
- [x] 1.2 In `_usage`, add `cache_write_tokens`: Anthropic `usage.cache_creation_input_tokens`;
  OpenAI `usage.prompt_tokens_details.cache_write_tokens`; `None` when absent. `StreamUsage`
  already merges streamed usage, so streamed Anthropic replies need no new parsing. In `route`,
  set `record["triage_verdict"]` to `"easy"` or `"did_step"` from the `_triage` result; never
  set it when the request was not triaged or triage raised. Verify the 1.1 tests pass
- [x] 1.3 Add both fields to the Turn log table in `docs/reference/http-api.md`

## Task 2: Step groups of the reply

- [x] 2.1 Write failing tests for every scenario under "Step groups of the reply" and "No
  secrets", including a streamed frontier reply whose chunks reach the client unchanged and in
  order; verify they fail
- [x] 2.2 Record `step_groups` for the reply that reached the agent: the group of each call from
  `thetarelay.steps.tool_calls` and `classify`, in order; `["irreversible"]` when
  `signals_tool_call` is true but no call can be read; `[]` for a non-200 status or a text-only
  reply. For streams, keep the relayed chunks in a buffer inside `relay()` as they pass and
  compute the groups in its `finally`, so no chunk is delayed. Verify the 2.1 tests pass
- [x] 2.3 Add `step_groups` to the Turn log table in `docs/reference/http-api.md`, and a
  `CHANGELOG.md` entry under `[Unreleased]` / `### Added`: the turn log records the step groups
  of each reply, cache-write tokens and the triage verdict
- [x] 2.4 Run `openspec validate 03-log-routing-signals --strict` and `just check`; mark all
  tasks `[x]`
