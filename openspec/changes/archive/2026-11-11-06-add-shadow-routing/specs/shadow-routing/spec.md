# Spec Delta

## Purpose

Collect real examples of what the lite model would have done on frontier steps, without
affecting what the agent receives, so the judge and its threshold can be checked on real work.

## ADDED Requirements

### Requirement: Shadow share

The system SHALL send a request routed to the frontier tier also to the lite tier with
probability `THETARELAY_SHADOW_SHARE` (default 0.0). Values outside 0.0 to 1.0 SHALL stop the
proxy from starting. No shadow SHALL be sent for triaged, escalated or oversized requests, or
when no turn log is set.

#### Scenario: Triaged and escalated requests

- **WHEN** a request is triaged, or escalated from the lite tier
- **THEN** no shadow request is sent for it

#### Scenario: Oversized request

- **WHEN** a request is larger than `THETARELAY_LITE_MAX_REQUEST_CHARS`
- **THEN** no shadow request is sent for it

#### Scenario: No turn log

- **WHEN** `THETARELAY_SHADOW_SHARE=1.0` and `THETARELAY_TURN_LOG` is not set
- **THEN** no shadow requests are sent

#### Scenario: Invalid share

- **WHEN** `THETARELAY_SHADOW_SHARE=1.5`
- **THEN** the proxy does not start

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

The system SHALL record a `shadow` object on the request's own turn log line, written once the
shadow finishes, with the tool calls of the frontier reply and of the shadow reply (each as
`name`, `group`, `target` and `args_sha256`) and `match`: `true` if both replies have the same
number of calls and each pair has the same name and the same file path or shell command. The
turn log SHALL NOT contain the arguments' text beyond `target`.

#### Scenario: Matching action

- **WHEN** the frontier reply and the shadow reply both call `read` on `src/app.py`
- **THEN** the turn log line has `"shadow": {"match": true, ...}`

#### Scenario: Different edit to the same file

- **WHEN** both replies call `edit` on `src/app.py` with different text
- **THEN** `match` is `true` and the two calls have different `args_sha256`

#### Scenario: One line per request

- **WHEN** a request has a shadow
- **THEN** the turn log has exactly one line for that request, and it is written after the
  shadow finishes or times out

### Requirement: Shadow cost and errors

When the lite tier answers a shadow with status 200, the `shadow` object SHALL have `cost_usd`,
the reported cost (`null` if not reported), apart from the line's own `cost_usd`. A failed shadow
SHALL record `{"error": ...}`: `timeout`, `http_<status>`, `parse`, `shutdown`, or the exception's
class name. A cut frontier stream SHALL be compared as far as it was relayed.

#### Scenario: Shadow cost

- **WHEN** the shadow lite reply reports a cost of 0.004 USD
- **THEN** the line has `"shadow": {..., "cost_usd": 0.004}`, and the line's own `cost_usd` is
  the frontier reply's

#### Scenario: Shadow error

- **WHEN** the lite tier answers the shadow with status 500
- **THEN** the line has `"shadow": {"error": "http_500"}`

### Requirement: Judge asked in every mode

The system SHALL ask the judge and record its answer on every request, also in `frontier`,
`triage`, `lite` and `random` modes, when `THETARELAY_SHADOW_SHARE` is above 0.0 and a turn log
is set. The answer SHALL NOT change routing in those modes; the agent waits for it.

#### Scenario: Judge error in frontier mode

- **WHEN** the route mode is `frontier`, the shadow share is 0.1 and the judge is down
- **THEN** the request still goes to the frontier tier and the line records `judge_error`

#### Scenario: No turn log

- **WHEN** the route mode is `frontier`, the shadow share is 0.1 and no turn log is set
- **THEN** the judge is not asked

#### Scenario: Frontier mode

- **WHEN** the route mode is `frontier` and the shadow share is 0.1
- **THEN** every turn log line has `p_easy`, and every request goes to the frontier tier
