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

- **WHEN** the upstream returns a status other than 200
- **THEN** the turn log line has `"step_groups": []`

#### Scenario: Streamed frontier reply

- **WHEN** a streamed frontier reply calls `bash` with `git push`
- **THEN** the turn log line has `"step_groups": ["irreversible"]`, and the stream reaches the
  agent without added delay

### Requirement: Cache writes

The system SHALL record `cache_write_tokens` from the provider's usage report when present
(Anthropic `cache_creation_input_tokens`), and `null` when the provider does not report it.

#### Scenario: OpenRouter cache write

- **WHEN** an OpenAI-format reply reports `prompt_tokens_details.cache_write_tokens: 1200`
- **THEN** the turn log line has `"cache_write_tokens": 1200`

#### Scenario: Not reported

- **WHEN** a reply's usage has no cache-write count
- **THEN** the turn log line has `"cache_write_tokens": null`

#### Scenario: Frontier call after a long lite streak

- **WHEN** a frontier reply reports `cache_read_input_tokens: 0` and
  `cache_creation_input_tokens: 48000`
- **THEN** the turn log line has `"cached_tokens": 0` and `"cache_write_tokens": 48000`

### Requirement: Triage verdict

The system SHALL record `triage_verdict` for every triaged request: `easy` when the frontier
handed the step to the lite tier, `did_step` when it answered itself. Requests that were not
triaged SHALL NOT have the field.

#### Scenario: Frontier hands the step back

- **WHEN** a triaged request's frontier reply is `<EASY/>`
- **THEN** the turn log line has `"triage_verdict": "easy"` and the judge summary in
  `judge_state`

#### Scenario: Frontier does the step

- **WHEN** a triaged request's frontier reply is a normal answer, streamed or not
- **THEN** the turn log line has `"triage_verdict": "did_step"`

#### Scenario: Not triaged

- **WHEN** the judge sends a request to the lite tier directly
- **THEN** the turn log line has no `triage_verdict` field

### Requirement: No secrets

The system SHALL NOT write request headers, API keys or credentials to the turn log.

#### Scenario: Key in header

- **WHEN** the client sends `x-api-key: sk-client`
- **THEN** no turn log line contains `sk-client`
