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
| `x-thetarelay-reason` | `judge`, `recent_failure (N requests ago)`, `too_large_for_lite` or `judge_error`. `; lite_failed`, `; lite_stopped`, `; lite_malformed`, `; lite_irreversible` or `; lite_wrote` is added if the lite tier failed, stopped without a tool call, or was discarded by the lite guard (see `THETARELAY_LITE_GUARD`), and the frontier tier answered. |
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
| `input_tokens`, `cached_tokens`, `cache_write_tokens`, `output_tokens`, `cost_usd` | Usage from the response (both streamed and non-streamed). `cost_usd` is the cost that OpenRouter reports. `cache_write_tokens` is `null` when the provider does not report it. `input_tokens`, `output_tokens` and `cost_usd` are absent on error replies. |
| `triage_cached_tokens`, `triage_cache_write_tokens` | Cache read and write tokens of the frontier triage call. Present only on triage requests that the frontier model handed back (`easy`). |
| `step_groups` | The step groups (`read`, `run`, `write`, `irreversible`) of the tool calls in the reply that reached the agent, in order. `[]` for a text-only reply or a status other than 200; `["irreversible"]` when a tool call signals but cannot be read. |
| `stream_cut` | `true` when a streamed reply ended early (upstream error or client disconnect). `step_groups` then comes from the bytes already relayed, and is `["irreversible"]` when a tool call had started. Absent when the stream finished. |
| `triage_verdict` | `easy` if the frontier model handed the step to the lite tier, `did_step` if it answered itself. Absent when the request was not triaged or the triage reply had a status other than 200. |
| `guarded_step_groups` | Only when the lite guard discarded a lite reply: the step groups of that reply's tool calls (`read`, `run`, `write` or `irreversible`). For a call that cannot be read it is `["irreversible"]`. |
| `lite_output_chars`, `discarded_lite_cost_usd` | When a lite reply was discarded, by the lite guard or by `lite_stopped`: the size of the reply in bytes and the cost OpenRouter reported for it (`null` if not reported). The line's other usage fields are those of the frontier call that replaced it. |
