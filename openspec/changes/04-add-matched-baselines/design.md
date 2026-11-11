# Design: matched baselines

## Context

`scripts/routing_eval/run.py` runs Pi on Aider exercises through ThetaRelay, one proxy per arm,
and has a `random` arm whose lite share comes from the `judge` arm's turn logs. Its report gives
pass counts and a Newcombe paired difference against `frontier`. `scripts/app_eval/run.py`
builds one spec with Claude Code and Pi arms and prints raw records.

## Decisions

- **Shared stats module.** `scripts/eval_stats.py` holds the confidence intervals both reports
  use: Wilson score for a pass rate, Newcombe hybrid score for a paired difference of pass rates
  (moved from `routing_eval`), and a percentile bootstrap (10,000 resamples, seed 0) for the mean
  cost per task and for the paired cost difference. Alternative: t intervals. Rejected: cost per
  task is skewed by a few long sessions.
- **Arm names.** A routed arm is `judge`, `triage` or `hybrid`. Its matched random arm is
  `random-<arm>` and runs `THETARELAY_ROUTE_MODE=random` with
  `THETARELAY_RANDOM_LITE_SHARE` set to the routed arm's lite share in the same results folder,
  formatted with three decimals. A random arm whose routed arm has no turn logs yet is skipped
  with a message. Plain `random` stays as an alias of `random-judge`.
- **Ablations use existing settings, no product change.** `hybrid-no-stop`:
  `THETARELAY_ESCALATE_ON_STOP=0`. `hybrid-no-triage`: `THETARELAY_ROUTE_MODE=judge` (hybrid
  without the triage step is judge mode). `hybrid-no-size-limit`:
  `THETARELAY_LITE_MAX_REQUEST_CHARS=1000000000`.
- **App specs.** Each spec is a folder `scripts/app_eval/specs/<name>/` with `starter/`
  (`SPEC.md`, `pyproject.toml`) and `hidden_tests/`. The current app moves to
  `specs/bookmarks/`. Two new specs: `todo-cli` (smaller: a CLI tool over a JSON file) and
  `inventory` (larger: two FastAPI services sharing one SQLite database). Hidden tests are
  checked against a reference build before they are committed.
- **App arms.** App eval gains the same routed, random and ablation arms through the existing
  Pi proxy setup; `claude` and `pi` (now an alias of `pi-hybrid`) stay. Results keys become
  `(spec, arm, run)`.
- **Pairing.** A routed arm and its random arm are paired by `(run, exercise)` in routing eval
  and `(spec, run)` in app eval. Pairs with a missing side are left out and counted.

## Risks / Trade-offs

- [Cost of a full run] → Defaults stay small for local use (`--runs 1`); the release run uses
  `--runs 10` and is documented, not run in CI.
- [Lite share drifts between runs] → Each run of a random arm reads the share at its start,
  from all completed runs of its routed arm, and records it in its attempts.
- [Escalated lite calls are not logged] → A lite reply that would end the turn and goes to the
  frontier (`lite_stopped`) is logged only as the frontier call. Its lite cost and lite tier are
  missing from both reports and from the share random arms copy. Small, since lite calls are
  cheap; a turn-log follow-up.
