# HTTP API, headers and turn log

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/messages` | Anthropic Messages API. Streaming is supported. |
| `POST` | `/v1/chat/completions`, `/chat/completions` | OpenAI Chat Completions API. Streaming is supported. |
| `GET` | `/health` | Returns `{"service": "thetarelay", "status": "healthy"}`. |
| `GET` | `/stats`, `/thetarelay/stats` | Configuration and request counts per tier (`local_requests`, `frontier_requests`, `local_errors`, `frontier_errors`). |

ThetaRelay forwards the request body without change, except:

- `model` is set to the model of the tier.
- For the local tier, thinking is turned off (see `THETARELAY_LOCAL_DISABLE_THINKING`).

## Response headers

| Header | Value |
|---|---|
| `x-thetarelay-tier` | `local` or `frontier` |
| `x-thetarelay-model` | Model that wrote the response |
| `x-thetarelay-reason` | `judge`, `recent_failure (N requests ago)`, `too_large_for_local` or `judge_error`. `; local_failed` is added if the local server failed and the frontier tier answered. |
| `x-thetarelay-p-easy` | The judge's P(easy), if the judge was asked |

## Turn log

If `THETARELAY_TURN_LOG` is set, ThetaRelay appends one JSON line for each request. The file
contains prompt text, so keep it private. It never contains request headers or API keys.

| Field | Value |
|---|---|
| `ts` | Time the request finished, ISO 8601, UTC |
| `provider` | `anthropic` or `openai` |
| `conversation` | First 12 hex characters of the SHA-256 of the first user message |
| `messages` | Number of messages in the request |
| `stream` | `true` if the request was streamed |
| `client_model` | Model name that the agent sent |
| `tier`, `model`, `reason` | The decision. Same values as the headers. |
| `p_easy`, `threshold`, `judge_ms`, `judge_error` | The judge's answer, the threshold, the judge time and the judge error |
| `judge_state` | The summary that the judge read |
| `status`, `upstream_ms` | Status code and time of the model request |
| `input_tokens`, `cached_tokens`, `output_tokens`, `cost_usd` | Usage from the response. Only for requests that are not streamed. `cost_usd` is the cost that OpenRouter reports. |
