# Tasks

Spec: `specs/shadow-routing/spec.md`. Design: `design.md`. Code in `src/thetarelay/config.py`,
`router.py` and `proxy.py`; tests in `tests/test_router.py` with `httpx.MockTransport`. `just
check` must pass after each task. Tests never call real APIs.

## Task 1: Setting and judge in every mode

- [x] 1.1 Write failing tests for "Shadow share" ("Off by default", "Invalid share") and "Judge
  asked in every mode" ("Frontier mode", "Judge error in frontier mode", plus `lite`, `random`
  and `triage` modes keeping their routing); verify they fail
- [x] 1.2 Add `shadow_share: float = 0.0` to `RouterConfig` (`THETARELAY_SHADOW_SHARE`,
  validated 0.0–1.0). In `decide()`, when `shadow_share > 0`, ask the judge in every mode and
  record its fields without changing tier or reason; verify the tests pass
- [x] 1.3 Add the setting to `docs/reference/configuration.md`

## Task 2: Shadow requests and logging

- [x] 2.1 Write failing tests for "Shadow replies never reach the agent", "Shadow requests follow
  lite-tier rules", "Shadow results are logged" (all scenarios) and "Triaged and escalated
  requests", streamed and not, both API formats where it applies; use a deterministic coin
  (monkeypatch `random.random`) and an `asyncio.Event` or short sleeps in the mock transport to
  check the agent is not delayed; verify they fail
- [x] 2.2 Implement per `design.md`: start the shadow (`_outbound(..., "lite", ...)` on the
  client's body) as a background task when chosen; keep tasks in a set cancelled at shutdown;
  defer this request's `recorder.record` until the shadow finishes or times out
  (`lite_timeout_seconds`); build the `shadow` object (name, group, target, args_sha256, match,
  or error) from `steps.tool_calls` of both replies; verify the tests pass

## Task 3: Wrap-up

- [x] 3.1 Document the `shadow` field and the late-written line in `docs/reference/http-api.md`
  and shadow routing in `docs/explanation/how-routing-works.md` (extra lite load and cost);
  add a `CHANGELOG.md` entry under `[Unreleased]` / `### Added`
- [x] 3.2 Run `openspec validate 06-add-shadow-routing --strict` and `just check`; mark all tasks
  `[x]`
