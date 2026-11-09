# Spec Delta

## Purpose

Collect real examples of what the lite model would have done on frontier steps, without
affecting what the agent receives, so the judge and its threshold can be checked on real work.

## ADDED Requirements

### Requirement: Shadow share

The system SHALL send a request routed to the frontier tier also to the lite tier with
probability `THETARELAY_SHADOW_SHARE` (default 0.0). Values outside 0.0 to 1.0 SHALL stop the
proxy from starting.

#### Scenario: Off by default

- **WHEN** `THETARELAY_SHADOW_SHARE` is not set
- **THEN** no shadow requests are sent

### Requirement: Shadow replies never reach the agent

The system SHALL return only the frontier reply to the agent, and the agent's response time
SHALL NOT wait for the shadow request.

#### Scenario: Slow lite server

- **WHEN** the shadow lite request takes 20 s and the frontier reply takes 3 s
- **THEN** the agent receives the frontier reply after about 3 s

#### Scenario: Lite server down

- **WHEN** the shadow lite request fails
- **THEN** the agent receives the frontier reply as usual, and the turn log records the shadow
  error

### Requirement: Shadow requests follow lite-tier rules

The system SHALL send shadow requests exactly as it sends lite-tier requests: body unchanged
except `model` and thinking off, and only `THETARELAY_LITE_API_KEY` as credentials.

#### Scenario: Credentials

- **WHEN** the client sends `authorization: Bearer sk-client`
- **THEN** the shadow request carries no client credentials

### Requirement: Shadow results are logged

The system SHALL record a `shadow` object on the request's turn log line with the lite reply's
tool calls (name and arguments), their step groups, and `match` (`true` if each lite call has
the same tool and the same file path or shell command as the frontier call at the same position).

#### Scenario: Matching action

- **WHEN** the frontier reply and the shadow reply both call `read` on `src/app.py`
- **THEN** the turn log line has `"shadow": {"match": true, ...}`

### Requirement: Judge asked in every mode

The system SHALL ask the judge and record its answer on every request, also in `frontier`,
`triage`, `lite` and `random` modes, when `THETARELAY_SHADOW_SHARE` is above 0.0. The answer
SHALL NOT change routing in those modes.

#### Scenario: Frontier mode

- **WHEN** the route mode is `frontier` and the shadow share is 0.1
- **THEN** every turn log line has `p_easy`, and every request goes to the frontier tier
