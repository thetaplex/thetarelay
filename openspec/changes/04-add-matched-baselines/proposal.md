# Proposal: add matched baselines

## Why

The strongest evidence that the judge adds value is beating random routing at the same lite
share. `scripts/routing_eval` already runs a `random` arm with the share taken from the `judge`
arm, but only for the `judge` mode, with one run by default and no confidence intervals.
`scripts/app_eval` has one spec, no random arm, and arms only for Claude Code and Pi. The 32 %
saving in `docs/reference/tested-models.md` rests on that one spec.

## What Changes

- `routing_eval`: a matched random arm for each routed arm (`judge`, `triage`, `hybrid`), each
  with the lite share of the arm it matches. Ablation arms switch one rule off at a time
  (`escalate_on_stop`, the triage step, the size limit).
- `app_eval`: at least three specs (the current one plus two of different size), and the same
  arms as `routing_eval`.
- Both reports: per arm, mean and 95 % confidence interval of cost per task and of hidden-test
  pass rate, and the paired difference to the matched random arm.
- Runs: `--runs 1` by default for local use; the documented release run uses `--runs 10`.

## Capabilities

### New Capabilities

- `routing-evaluation`: the evaluation harnesses and their reports.

### Modified Capabilities

None.

## Impact

- `scripts/routing_eval/run.py`, `scripts/app_eval/run.py`, new specs under
  `scripts/app_eval/`.
- Cost: about 10 runs × 12 arms × 3 specs (360 agent sessions). Run once per release, not in CI.
- The research protocol (`paper/PROTOCOL.md` on `research/paper`) should reference these arms
  rather than define its own.

## Decided

- Extra app specs: `todo-cli` (smaller, a CLI tool) and `inventory` (larger, two services
  sharing a database). See `design.md`.
