# Spec Delta

## Purpose

Let the frontier model decide what to do and the lite model carry it out, by routing on the
plan the frontier states.

## ADDED Requirements

### Requirement: Lite streak follows a stated plan

In route mode `plan`, after a frontier reply that states a plan of steps, the system SHALL send
following requests to the lite tier while the plan has steps left and no step has failed.

#### Scenario: Plan with three steps

- **WHEN** the frontier reply says it will read `app.py`, edit `tests/test_app.py`, and run
  `pytest`
- **THEN** the next three requests go to the lite tier, and the request after them goes to the
  frontier tier

### Requirement: Return to the frontier

The system SHALL send the request to the frontier tier when a step fails, when a lite step's
target is not in the remaining plan, or when the frontier reply states no plan.

#### Scenario: Failure during the plan

- **WHEN** the `pytest` step fails
- **THEN** the next request goes to the frontier tier

#### Scenario: Off-plan step

- **WHEN** a lite step edits `src/other.py`, which is not in the plan
- **THEN** that lite reply is discarded and the request goes to the frontier tier
