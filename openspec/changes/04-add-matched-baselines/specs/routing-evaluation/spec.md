# Spec Delta

## Purpose

Measure whether ThetaRelay's routing saves cost at equal quality, against baselines that make
the comparison fair, with uncertainty reported.

## ADDED Requirements

### Requirement: Matched random arms

Each harness SHALL run, for each routed arm, a random arm whose lite share equals the routed
arm's measured lite share from the same run.

#### Scenario: Routed arm not run yet

- **WHEN** the arm `random-triage` is asked for and no `triage` turn logs exist
- **THEN** the harness skips it with a message instead of guessing a share

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

### Requirement: Uncertainty in reports

Each report SHALL give, per arm, the number of runs, the mean and 95 % confidence interval of
cost per task and of hidden-test pass rate, and the paired difference to the matched random arm
with its confidence interval.

#### Scenario: Paired difference

- **WHEN** `hybrid` and `random-hybrid` both have 10 runs of the same tasks
- **THEN** the report shows the difference in pass rate (Newcombe) and in cost per task
  (bootstrap), each with a 95 % interval

#### Scenario: Too few runs

- **WHEN** an arm has fewer than 3 runs
- **THEN** the report shows its numbers without an interval and marks them as too few runs
