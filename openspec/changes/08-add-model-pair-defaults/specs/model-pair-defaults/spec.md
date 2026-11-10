# Spec Delta

## Purpose

Pick a route mode that works for the configured frontier model when the user has not chosen
one, based on measured behaviour of model families.

## ADDED Requirements

### Requirement: Default mode by frontier model

When `THETARELAY_ROUTE_MODE` is not set, the system SHALL use `judge` for frontier models whose
name starts with `openai/`, and `hybrid` for all other frontier models.

#### Scenario: OpenAI frontier model

- **WHEN** `THETARELAY_FRONTIER_MODEL=openai/gpt-6.1-terra` and no route mode is set
- **THEN** the route mode is `judge`

#### Scenario: Explicit mode wins

- **WHEN** `THETARELAY_FRONTIER_MODEL=openai/gpt-6.1-terra` and `THETARELAY_ROUTE_MODE=hybrid`
- **THEN** the route mode is `hybrid`

### Requirement: Start-up message

The system SHALL print the route mode at start-up and, when it was chosen by model, the reason.

#### Scenario: Chosen by model

- **WHEN** the route mode was chosen because the frontier model is an OpenAI model
- **THEN** the start-up output says the mode is `judge` because OpenAI models do not hand steps
  to the lite tier
