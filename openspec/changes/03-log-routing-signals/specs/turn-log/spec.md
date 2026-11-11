# Spec Delta

## Purpose

Record one JSON line per proxied request with the routing decision, the step taken and its
cost, so routing can be evaluated and improved from real sessions without request headers or
secrets.

## ADDED Requirements

### Requirement: Step groups of the reply

The system SHALL record `step_groups`, the list of step groups (`read`, `run`, `write`, `irreversible`)
of the tool calls in the reply that reached the agent, in order. A reply with no tool call SHALL
have an empty list.

#### Scenario: Lite reply with two calls

- **WHEN** the agent receives a reply calling `read` then `edit`
- **THEN** the turn log line has `"step_groups": ["read", "write"]`

#### Scenario: Unreadable tool call

- **WHEN** a reply signals a tool call whose name or arguments cannot be read
- **THEN** the turn log line has `"step_groups": ["irreversible"]`

#### Scenario: Error reply

- **WHEN** the upstream returns a status other than 200 and the reply is not a stream cut short
- **THEN** the turn log line has `"step_groups": []`

#### Scenario: Streamed frontier reply

- **WHEN** a streamed frontier reply calls `bash` with `git push`
- **THEN** the turn log line has `"step_groups": ["irreversible"]`, and the stream reaches the
  agent without added delay

### Requirement: Streams cut short

When a streamed reply ends early, from an upstream error or the agent disconnecting, the system
SHALL record `"stream_cut": true` and take `step_groups` from the bytes already relayed,
whatever the status. A tool call that had started but cannot be read SHALL count as
`irreversible`. A stream that finished SHALL NOT have the field.

#### Scenario: Upstream drops mid tool call

- **WHEN** a streamed reply is cut after a `bash` tool call has started but before its arguments
  are complete
- **THEN** the turn log line has `"stream_cut": true` and `"step_groups": ["irreversible"]`

#### Scenario: Cut before any tool call

- **WHEN** a streamed reply is cut while it is still sending text
- **THEN** the turn log line has `"stream_cut": true` and `"step_groups": []`

### Requirement: Cache writes

The system SHALL record `cache_write_tokens` from the provider's usage report when present
(Anthropic `cache_creation_input_tokens`; OpenRouter `prompt_tokens_details.cache_write_tokens`),
and `null` when the provider does not report it. `cached_tokens` and `cache_write_tokens` SHALL
be on every line, `null` on error replies and streams without usage.

#### Scenario: OpenRouter cache write

- **WHEN** an OpenAI-format reply reports `prompt_tokens_details.cache_write_tokens: 1200`
- **THEN** the turn log line has `"cache_write_tokens": 1200`

#### Scenario: Not reported

- **WHEN** a reply's usage has no cache-write count
- **THEN** the turn log line has `"cache_write_tokens": null`

#### Scenario: Error reply has cache fields

- **WHEN** the upstream returns status 500
- **THEN** the turn log line has `"cached_tokens": null` and `"cache_write_tokens": null`

#### Scenario: Frontier call after a long lite streak

- **WHEN** a frontier reply reports `cache_read_input_tokens: 0` and
  `cache_creation_input_tokens: 48000`
- **THEN** the turn log line has `"cached_tokens": 0` and `"cache_write_tokens": 48000`

### Requirement: Triage verdict

The system SHALL record `triage_verdict` for every triaged request whose frontier reply has
status 200: `easy` when the frontier handed the step to the lite tier, `did_step` when it
answered itself. Requests that were not triaged, or whose triage reply was an error, SHALL NOT
have the field. When the frontier hands the step back, the system SHALL also record that call's
cache use as `triage_cached_tokens` and `triage_cache_write_tokens`.

#### Scenario: Frontier hands the step back

- **WHEN** a triaged request's frontier reply is `<EASY/>`
- **THEN** the turn log line has `"triage_verdict": "easy"`, the judge summary in
  `judge_state`, and the triage call's `triage_cached_tokens` and `triage_cache_write_tokens`

#### Scenario: Frontier does the step

- **WHEN** a triaged request's frontier reply is a normal answer, streamed or not
- **THEN** the turn log line has `"triage_verdict": "did_step"`

#### Scenario: Triage reply is an error

- **WHEN** a triaged request's frontier reply has status 429
- **THEN** the turn log line has no `triage_verdict` field

#### Scenario: Not triaged

- **WHEN** the judge sends a request to the lite tier directly
- **THEN** the turn log line has no `triage_verdict` field

### Requirement: No secrets

The system SHALL NOT write request headers, API keys or credentials to the turn log.

#### Scenario: Key in header

- **WHEN** the client sends `x-api-key: sk-client`
- **THEN** no turn log line contains `sk-client`
