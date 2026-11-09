# Spec Delta

## Purpose

Show a user, from their own turn log, what routing cost and what the same requests would have
cost on the frontier tier alone.

## ADDED Requirements

### Requirement: Report from a turn log

The command `thetarelay report <path>` SHALL print, per tier: requests, input, cached, cache
write and output tokens, and cost; and in total: actual cost, estimated frontier-only cost, and
the saving as a percentage.

#### Scenario: Mixed log

- **WHEN** the log has 60 lite and 40 frontier requests with `cost_usd` set
- **THEN** the report shows both tiers, the total of `cost_usd`, and the estimate

### Requirement: Labelled estimate

The report SHALL label the frontier-only cost as an estimate and state its assumption: lite
requests priced at frontier prices with the same token counts.

#### Scenario: Output text

- **WHEN** the report prints the frontier-only cost
- **THEN** the line includes the word "estimate"

### Requirement: Fallbacks by reason

The report SHALL count requests by routing reason, including fallback suffixes such as
`lite_stopped` and `lite_failed`.

#### Scenario: Fallbacks present

- **WHEN** 5 requests have a reason ending in `; lite_stopped`
- **THEN** the report lists `lite_stopped: 5`

### Requirement: Missing data

The report SHALL skip lines that are not valid JSON and count them, and SHALL show `n/a` for a
cost it cannot compute.

#### Scenario: Prices unavailable offline

- **WHEN** the report runs with `--offline` and no prices are given
- **THEN** the frontier-only cost shows `n/a` and the actual cost is still shown
