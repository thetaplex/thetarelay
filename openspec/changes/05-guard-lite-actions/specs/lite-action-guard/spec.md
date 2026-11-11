# Spec Delta

## Purpose

Check what the lite model actually does before the agent runs it, and hand risky or malformed
actions to the frontier tier.

## ADDED Requirements

### Requirement: Guard levels

The system SHALL support a lite guard level of `off`, `irreversible` or `writes`, set by
`THETARELAY_LITE_GUARD`, with `off` as the default. An unknown value SHALL stop the proxy from
starting with an error that lists the valid values.

#### Scenario: Default

- **WHEN** `THETARELAY_LITE_GUARD` is not set
- **THEN** lite replies are not checked by the guard

#### Scenario: Invalid value

- **WHEN** `THETARELAY_LITE_GUARD=strict`
- **THEN** the proxy does not start and the error names `off`, `irreversible` and `writes`

### Requirement: Irreversible actions go to the frontier

At level `irreversible` or `writes`, the system SHALL discard a lite reply containing an
`irreversible` tool call and send the original request to the frontier tier, with reason suffix
`lite_irreversible`.

#### Scenario: Lite model pushes

- **WHEN** the lite reply calls `bash` with `git push origin main`
- **THEN** the agent receives the frontier reply, and the `x-thetarelay-reason` header ends with
  `; lite_irreversible`

### Requirement: Malformed tool calls go to the frontier

At level `irreversible` or `writes`, the system SHALL discard a lite reply whose tool call cannot
be read, names a tool not in the request's `tools`, has arguments that are not a JSON object, or
lacks an argument the tool schema lists as required, and send the request to the frontier tier
with reason suffix `lite_malformed`. A request without `tools` makes every call malformed. Tool
entries without a name SHALL be skipped.

#### Scenario: Arguments not an object

- **WHEN** a lite reply calls `bash` with arguments that are not a JSON object
- **THEN** the agent receives the frontier reply, with the reason suffix `; lite_malformed`

#### Scenario: Unknown tool

- **WHEN** the request offers `read`, `bash`, `edit`, `write` and the lite reply calls `run_tests`
- **THEN** the agent receives the frontier reply with reason suffix `; lite_malformed`

#### Scenario: Missing required argument

- **WHEN** the `edit` tool requires `path` and the lite reply calls `edit` without `path`
- **THEN** the agent receives the frontier reply with reason suffix `; lite_malformed`

#### Scenario: Unreadable tool call

- **WHEN** a lite reply signals a tool call that cannot be read
- **THEN** the agent receives the frontier reply with reason suffix `; lite_malformed`, and the
  turn log has `"guarded_step_groups": ["irreversible"]`

#### Scenario: Tool without a name

- **WHEN** the request's `tools` has an entry without a name and the lite reply calls `read`,
  which the request offers
- **THEN** the guard checks the `read` call as usual and does not discard the reply for the
  nameless entry

### Requirement: Writes go to the frontier at level writes

At level `writes`, the system SHALL also discard a lite reply containing a `write` tool call and
send the request to the frontier tier with reason suffix `lite_wrote`.

#### Scenario: Lite model edits

- **WHEN** the guard level is `writes` and the lite reply calls `edit`
- **THEN** the agent receives the frontier reply with reason suffix `; lite_wrote`

#### Scenario: Lite model runs tests

- **WHEN** the guard level is `writes` and the lite reply calls `bash` with `uv run pytest -q`
- **THEN** the agent receives the lite reply unchanged

#### Scenario: Lite model reads

- **WHEN** the guard level is `writes` and the lite reply calls `read` and `bash` with `rg foo`
- **THEN** the agent receives the lite reply unchanged

### Requirement: One reason per discard

When several checks apply to one lite reply, the system SHALL use the first in this order:
`lite_malformed`, `lite_irreversible`, `lite_wrote`, `lite_stopped`. Forced lite mode
(`THETARELAY_ROUTE_MODE=lite`) SHALL NOT be guarded, and a guarded streamed reply SHALL reach
the agent only after the check.

#### Scenario: Malformed and irreversible

- **WHEN** a lite reply calls `bash` with `git push` and also calls `run_tests`, which the request
  does not offer
- **THEN** the reason suffix is `; lite_malformed`, and the turn log has
  `"guarded_step_groups": ["irreversible"]`

#### Scenario: Forced lite mode

- **WHEN** `THETARELAY_ROUTE_MODE=lite` and the guard level is `writes`, and the lite reply calls
  `edit`
- **THEN** the agent receives the lite reply unchanged

#### Scenario: Streamed lite reply

- **WHEN** a streamed lite reply calls `bash` with `git push`, the guard level is `irreversible`
  and `THETARELAY_ESCALATE_ON_STOP=false`
- **THEN** the agent receives the frontier reply, and no part of the lite stream reaches it

### Requirement: Guarded replies are logged

The system SHALL record in the turn log, for every discarded lite reply (by the guard or by
`lite_stopped`), its size in bytes as `lite_output_chars` and its reported cost as
`discarded_lite_cost_usd`, and for a guard discard its step groups as `guarded_step_groups`. It
SHALL NOT send any part of the discarded reply to the frontier tier. The evaluation reports
SHALL count `discarded_lite_cost_usd` in cost per task.

#### Scenario: Turn log of a discard

- **WHEN** the guard discards a lite reply that calls `edit` and reports a cost of 0.012 USD
- **THEN** the turn log line has `"guarded_step_groups": ["write"]`, `lite_output_chars`, and
  `"discarded_lite_cost_usd": 0.012`

#### Scenario: Stopped lite reply

- **WHEN** `lite_stopped` discards a lite reply that reports a cost of 0.012 USD
- **THEN** the turn log line has `"discarded_lite_cost_usd": 0.012`

#### Scenario: Kept lite reply

- **WHEN** the agent receives the lite reply
- **THEN** the turn log line has no `discarded_lite_cost_usd` field

#### Scenario: Frontier request after a discard

- **WHEN** a lite reply is discarded by the guard
- **THEN** the frontier request body equals the client's body except `model`
