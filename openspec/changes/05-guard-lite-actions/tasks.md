# Tasks

Spec: `specs/lite-action-guard/spec.md`. Design: `design.md`. Code in `src/thetarelay/config.py`
and `src/thetarelay/proxy.py`; tests in `tests/test_router.py` (proxy tests with
`httpx.MockTransport`). `just check` must pass after each task. Tests never call real APIs.

## Task 1: Guard setting

- [x] 1.1 Write failing tests for "Guard levels" ("Default", "Invalid value"); verify they fail
- [x] 1.2 Add `lite_guard: str = "off"` to `RouterConfig`, read from `THETARELAY_LITE_GUARD`,
  validated in `__post_init__` with an error that names `off`, `irreversible` and `writes`;
  verify the tests pass
- [x] 1.3 Add the setting to `docs/reference/configuration.md`

## Task 2: The guard

- [x] 2.1 Write failing tests for every scenario under "Irreversible actions go to the frontier",
  "Malformed tool calls go to the frontier", "Writes go to the frontier at level writes" and
  "Guarded replies are logged", in both API formats where the scenario applies, streamed and
  not; verify they fail
- [x] 2.2 Add a pure function `guard_reason(cfg, body, raw) -> str | None` that returns
  `lite_malformed`, `lite_irreversible`, `lite_wrote` or `None` per `design.md` (malformed check
  against the request's `tools`, in both formats); make it fail closed (`lite_malformed`) on any
  exception
- [x] 2.3 In `route()`, buffer the lite reply when `escalate_on_stop` is true or `lite_guard` is
  not `off` (never in forced lite mode); apply `guard_reason` before the stop check; on a
  discard, record `guarded_step_groups` and `lite_output_chars` and escalate with the reason;
  verify the 2.1 tests and the existing tests pass

## Task 3: Wrap-up

- [x] 3.1 Document the new reason suffixes in `docs/reference/http-api.md`, the guard in
  `docs/explanation/how-routing-works.md`, and the turn-log fields; add a `CHANGELOG.md` entry
  under `[Unreleased]` / `### Added`
- [x] 3.2 Run `openspec validate 05-guard-lite-actions --strict` and `just check`; mark all tasks
  `[x]`
