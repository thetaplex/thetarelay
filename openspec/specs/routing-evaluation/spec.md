# routing-evaluation Specification

## Purpose
Measure whether ThetaRelay's routing saves cost at equal quality, against baselines that make
the comparison fair, with uncertainty reported.

## Requirements

### Requirement: Matched random arms

Each harness SHALL run, for each routed arm, a random arm whose lite share equals the routed
arm's lite share over all its completed runs in the same results folder, read when each run of
the random arm starts and recorded with its attempts. `random` SHALL be an alias of
`random-judge`, and the app harness's `pi` an alias of `pi-hybrid`.

#### Scenario: Routed arm not run yet

- **WHEN** the arm `random-triage` is asked for and no `triage` turn logs exist
- **THEN** the harness skips it with a message instead of guessing a share

#### Scenario: Alias

- **WHEN** the arm `random` runs after `judge`
- **THEN** it uses the lite share of the `judge` turn logs, as `random-judge` does

#### Scenario: Hybrid sends 40 % lite

- **WHEN** the `hybrid` arm's turn logs show 40 % of requests on the lite tier
- **THEN** its matched random arm runs with `THETARELAY_RANDOM_LITE_SHARE=0.400`

### Requirement: Ablation arms

Each harness SHALL support arms that switch off one routing rule at a time while keeping the
rest of the `hybrid` configuration: no stop escalation, no triage, no size limit.

#### Scenario: Ablation settings

- **WHEN** the arm `hybrid-no-triage` runs
- **THEN** its proxy runs with `THETARELAY_ROUTE_MODE=judge` and every other setting of `hybrid`

#### Scenario: Ablation listed in the report

- **WHEN** the arms `hybrid` and `hybrid-no-triage` have run
- **THEN** the report shows both with their cost and pass rate side by side

### Requirement: Several app specs

The app harness SHALL build at least three different specs, each with its own hidden tests.

#### Scenario: Run all specs

- **WHEN** the app harness runs without a spec filter
- **THEN** every arm builds every spec

### Requirement: Safe runs and resumes

The app harness SHALL check every requested arm name before the first build, ignore earlier
result rows that record no spec, and delete a build's stale turn log before it starts again.

#### Scenario: Typo in an arm

- **WHEN** the app harness is started with `--arms claude,pi-hybird`
- **THEN** it exits with `unknown arm: pi-hybird` before any build starts

#### Scenario: Old rows without a spec

- **WHEN** `builds.jsonl` has rows from before specs existed
- **THEN** the harness ignores them for resuming and for the report, and says how many

#### Scenario: Resumed build

- **WHEN** a build starts again after a killed session left rows in its turn log
- **THEN** the old turn log is deleted first, so the new build's cost and lite share do not
  include them

### Requirement: Uncertainty in reports

Each report SHALL give, per arm, the number of runs, the mean and 95 % confidence interval of
cost per task and of hidden-test pass rate, and the paired difference to the matched random arm
with its confidence interval. In the app harness a build passes when all its hidden tests
pass, and the pass rate is the share of passing builds. Cost per task SHALL include frontier
triage calls that handed the step back.

#### Scenario: Paired difference

- **WHEN** `hybrid` and `random-hybrid` both have 10 runs of the same tasks
- **THEN** the report shows the difference in pass rate (Newcombe) and in cost per task
  (bootstrap), each with a 95 % interval

#### Scenario: Triage call cost

- **WHEN** a routing eval run has a frontier request costing 0.5 USD and a handed-back request
  with `cost_usd` 0.1 and `triage_cost_usd` 0.4, over 2 tasks
- **THEN** its cost per task is 0.500 USD

#### Scenario: Too few runs

- **WHEN** an arm has fewer than 3 runs
- **THEN** the report shows its numbers without an interval and marks them as too few runs
