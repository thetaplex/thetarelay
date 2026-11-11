# Tasks

Spec: `specs/model-pair-defaults/spec.md`. Design: `design.md`. Code in
`src/thetarelay/config.py` and `src/thetarelay/cli.py`; tests in `tests/test_router.py` (config
tests) and the CLI tests. `just check` must pass after each task. Tests never call real APIs.

## Task 1: Default and start-up line

- [x] 1.1 Write failing tests for every scenario in the spec (use `monkeypatch.setenv` /
  `delenv` and `RouterConfig.from_env()`; for the start-up line, call the CLI's start-up print
  with `uvicorn.run` monkeypatched and read `capsys`); verify they fail
- [x] 1.2 Implement per `design.md`: resolve the mode in `from_env()`, add `route_mode_reason`,
  print the route mode line at start-up; verify the tests pass
- [x] 1.3 Update `docs/reference/configuration.md` (the `THETARELAY_ROUTE_MODE` default) and
  `docs/reference/tested-models.md` (OpenAI row: the default is `judge`, which avoids the
  triage cost; no saving is claimed); add a `CHANGELOG.md` entry under `[Unreleased]` /
  `### Changed`
- [x] 1.4 Run `openspec validate 08-add-model-pair-defaults --strict` and `just check`; mark all
  tasks `[x]`
