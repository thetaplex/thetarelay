# Tasks

Spec: `specs/routing-evaluation/spec.md`. Design: `design.md`. No product code changes; the
harnesses live in `scripts/`. Unit tests for script logic go in `tests/test_eval_harness.py`
and import from `scripts/` by path (see how `tests/test_step_eval.py` reads script data). Tests
never start real agents or call real APIs. `just check` must pass after each task.

## Task 1: Shared statistics

- [ ] 1.1 Write failing tests in `tests/test_eval_harness.py` for `scripts/eval_stats.py`:
  Wilson interval of 7/10, Newcombe paired difference on a known table (copy the expected
  values from the current `routing_eval` function's output before moving it), bootstrap mean
  and paired-difference intervals (seed 0, deterministic), and "fewer than 3 runs gives no
  interval"; verify they fail
- [ ] 1.2 Add `scripts/eval_stats.py` with `wilson`, `newcombe`, `bootstrap_mean`,
  `bootstrap_paired_diff` (10,000 resamples, seed 0) and `MIN_RUNS = 3`; make
  `scripts/routing_eval/run.py` import `newcombe` from it; verify the tests pass

## Task 2: Matched random and ablation arms in routing eval

- [ ] 2.1 Write failing tests for "Matched random arms" (share from the routed arm's turn logs,
  three decimals; "Routed arm not run yet" skips) and "Ablation arms" ("Ablation settings" for
  all three), testing the arm-to-environment function without starting a proxy; verify they fail
- [ ] 2.2 Add the arms from `design.md` to `routing_eval`: `random-<arm>` for `judge`, `triage`,
  `hybrid`; `hybrid`, `triage`; the three `hybrid-no-*` ablations; keep `random` as an alias of
  `random-judge`. Record the share used in each random attempt. Verify the tests pass

## Task 3: Reports with uncertainty

- [ ] 3.1 Write failing tests for "Uncertainty in reports", "Paired difference" and "Too few
  runs", on small hand-made attempts and turn-log fixtures written to `tmp_path`; verify they
  fail
- [ ] 3.2 Rewrite the `routing_eval` report: per arm, runs, pass rate with Wilson interval, cost
  per task with bootstrap interval, lite share, and for each routed arm the paired difference
  to its random arm (pass rate: Newcombe; cost: bootstrap). Arms with fewer than 3 runs show
  numbers without intervals and are marked `too few runs`. Verify the tests pass

## Task 4: App eval specs and arms

- [ ] 4.1 Move the current app to `scripts/app_eval/specs/bookmarks/` (`starter/`,
  `hidden_tests/`, `reference/`) with `git mv`; update `run.py` paths, `pyproject.toml` ruff
  per-file ignores and `docs/reference/tested-models.md` commands
- [ ] 4.2 Add `specs/todo-cli/` and `specs/inventory/` per `design.md`, each with `starter/`
  (`SPEC.md`, `pyproject.toml`), `hidden_tests/` and a `reference/` build; verify each
  reference build passes its own hidden tests with `uv run pytest`, and record the counts
- [ ] 4.3 Write failing tests for "Several app specs" ("Run all specs") and app-eval arm
  environments; add a `--specs` filter (default: all), the routed, random and ablation arms
  (`pi` as an alias of `pi-hybrid`), results keyed by `(spec, arm, run)`, and the shared report
  from Task 3; verify the tests pass

## Task 5: Wrap-up

- [ ] 5.1 Document the release run (`--runs 10`, all arms, all specs, rough cost) in
  `docs/reference/tested-models.md`, written for users; add a `CHANGELOG.md` entry under
  `[Unreleased]` / `### Changed` for the evaluation harnesses
- [ ] 5.2 Run `openspec validate 04-add-matched-baselines --strict` and `just check`; mark all
  tasks `[x]`
