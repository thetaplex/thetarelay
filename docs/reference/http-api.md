# HTTP API, headers and turn log

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/messages` | Anthropic Messages API. Streaming is supported. |
| `POST` | `/v1/chat/completions`, `/chat/completions` | OpenAI Chat Completions API. Streaming is supported. |
| `GET` | `/health` | Returns `{"service": "thetarelay", "status": "healthy"}`. |
| `GET` | `/stats`, `/thetarelay/stats` | Configuration and request counts per tier (`lite_requests`, `frontier_requests`, `lite_errors`, `frontier_errors`). |

ThetaRelay forwards the request body without change, except:

- `model` is set to the model of the tier.
- For the lite tier, thinking is turned off (see `THETARELAY_LITE_DISABLE_THINKING`).
- For OpenAI-format requests to an `anthropic/` frontier model without a `cache_control` marker, `"cache_control": {"type": "ephemeral"}` is added.

## Response headers

| Header | Value |
|---|---|
| `x-thetarelay-tier` | `lite` or `frontier` |
| `x-thetarelay-model` | Model that wrote the response |
| `x-thetarelay-reason` | `judge`, `recent_failure (N requests ago)`, `too_large_for_lite` or `judge_error`. `; lite_failed` or `; lite_stopped` is added if the lite server failed or stopped without a tool call and the frontier tier answered. |
| `x-thetarelay-p-easy` | The judge's P(easy), if the judge was asked |

## Turn log

If `THETARELAY_TURN_LOG` is set, ThetaRelay appends one JSON line for each request (file mode `0600`). The file
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
| `input_tokens`, `cached_tokens`, `output_tokens`, `cost_usd` | Usage from the response (both streamed and non-streamed). `cost_usd` is the cost that OpenRouter reports. |
