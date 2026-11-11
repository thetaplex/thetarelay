# Tasks

Spec: `specs/usage-report/spec.md`. Design: `design.md`. Code in `src/thetarelay/report.py` and
`src/thetarelay/cli.py`; tests in `tests/test_report.py`. Price lookups in tests use
`httpx.MockTransport`; tests never call real APIs. `just check` must pass after each task.

## Task 1: Report

- [x] 1.1 Write failing tests in `tests/test_report.py` for every scenario in the spec, using
  small turn logs written to `tmp_path`; verify they fail
- [x] 1.2 Add `src/thetarelay/report.py` per `design.md` (load, summarise, price lookup with an
  injectable `httpx.Client`, plain-text and `--json` output) and the `report` subcommand in
  `cli.py` (`PATH`, `--frontier-model`, `--offline`, `--price-input`, `--price-output`,
  `--price-cache-read`, `--price-cache-write`, `--json`); verify the tests pass

## Task 2: Docs

- [x] 2.1 Add the command to `docs/reference/cli.md`; add `docs/how-to/check-your-savings.md`
  (turn on the turn log, run the agent, run the report, read the estimate) and link it from
  `docs/README.md`; add a `CHANGELOG.md` entry under `[Unreleased]` / `### Added`
- [x] 2.2 Run `openspec validate 07-add-usage-report --strict` and `just check`; mark all tasks
  `[x]`
