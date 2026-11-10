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

At level `irreversible` or `writes`, the system SHALL discard a lite reply whose tool call names
a tool not in the request's `tools`, has arguments that are not valid JSON, or lacks an argument
the tool schema lists as required, and send the request to the frontier tier with reason suffix
`lite_malformed`.

#### Scenario: Arguments not an object

- **WHEN** a lite reply calls `bash` with arguments that are not a JSON object
- **THEN** the agent receives the frontier reply, with the reason suffix `; lite_malformed`

#### Scenario: Unknown tool

- **WHEN** the request offers `read`, `bash`, `edit`, `write` and the lite reply calls `run_tests`
- **THEN** the agent receives the frontier reply with reason suffix `; lite_malformed`

#### Scenario: Missing required argument

- **WHEN** the `edit` tool requires `path` and the lite reply calls `edit` without `path`
- **THEN** the agent receives the frontier reply with reason suffix `; lite_malformed`

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

### Requirement: Guarded replies are logged

The system SHALL record in the turn log the step group of a discarded lite reply and the length
of its output, and SHALL NOT send any part of the discarded reply to the frontier tier.

#### Scenario: Forced lite mode

- **WHEN** `THETARELAY_ROUTE_MODE=lite` and the guard level is `writes`, and the lite reply calls
  `edit`
- **THEN** the agent receives the lite reply unchanged

#### Scenario: Streamed lite reply

- **WHEN** a streamed lite reply calls `bash` with `git push`, the guard level is `irreversible`
  and `THETARELAY_ESCALATE_ON_STOP=false`
- **THEN** the agent receives the frontier reply, and no part of the lite stream reaches it

#### Scenario: Turn log of a discard

- **WHEN** the guard discards a lite reply that calls `edit`
- **THEN** the turn log line has `"guarded_step_groups": ["write"]` and `lite_output_chars`

#### Scenario: Frontier request after a discard

- **WHEN** a lite reply is discarded by the guard
- **THEN** the frontier request body equals the client's body except `model`
