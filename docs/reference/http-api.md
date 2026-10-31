# Reference: HTTP API

## Endpoints of `thetarelay proxy`

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/messages` | Anthropic Messages API. Streaming and non-streaming. |
| `POST` | `/v1/chat/completions` | OpenAI Chat Completions API. Streaming and non-streaming. |
| `POST` | `/chat/completions` | Same as `/v1/chat/completions`. |
| `GET` | `/health` | Service status and active settings. |
| `GET` | `/stats` | Same as `/thetarelay/stats`. |
| `GET` | `/thetarelay/stats` | Settings and metrics. |

The proxy has no authentication. It forwards the request headers of the client to the provider,
with these exceptions:

- It removes `host`, `content-length`, `transfer-encoding`, `connection`, `keep-alive`,
  `accept-encoding` and `content-encoding`.
- It removes all `x-thetarelay-*` headers.
- It sets `content-type: application/json`.

It forwards the query string to the provider.

## Endpoints of the Headroom extension

When thetarelay runs inside `headroom proxy`, it adds `GET /thetarelay/stats` only. Headroom serves
all other endpoints.

## Request headers

| Header | Value | Effect |
|---|---|---|
| `x-thetarelay-bypass` | `1`, `true` or `yes` | No draft for this request. |
| `x-headroom-bypass` | `1`, `true` or `yes` | No draft for this request. |

## Response headers

`thetarelay proxy` adds these headers to `/v1/messages` and `/chat/completions` responses.

| Header | Values | When |
|---|---|---|
| `x-thetarelay-drafted` | `true`, `false` | Always. |
| `x-thetarelay-mode` | `none`, `accept_swap`, `openai_prediction`, `context_only`, `prediction` | Always. `none` means no draft. |
| `x-thetarelay-draft-ms` | Number, one decimal place | Always. Draft time in milliseconds. With adaptive context enabled, consecutive turns benefit from local prompt cache reuse and show lower latency. |
| `x-thetarelay-accepted` | `true`, `false` | Non-streaming `accept_swap` responses with status 200. |
| `x-thetarelay-tokens-saved` | Integer | Non-streaming responses where the draft was accepted. |

For streaming responses, the headers go out before thetarelay knows if the draft was accepted.
Thus streaming responses do not have `x-thetarelay-accepted`.

## `GET /health` response

| Field | Type | Description |
|---|---|---|
| `service` | string | Always `thetarelay-proxy`. |
| `status` | string | Always `healthy`. |
| `headroom_middleware_active` | boolean | `true` if Headroom `CompressionMiddleware` is active. |
| `local_model` | string | Local draft model. |
| `local_base_url` | string | Local API base URL. |
| `strategy` | string | Configured strategy. |

## `GET /thetarelay/stats` response

The `config` object contains `local_base_url`, `local_model`, `strategy` and `accept_token`.
`thetarelay proxy` also adds `anthropic_upstream_url` and `openai_upstream_url`.

The `metrics` object contains the counters since the process started:

| Field | Description |
|---|---|
| `requests_total` | Requests received on the model endpoints. |
| `drafts_attempted` | Same as `requests_total`. |
| `drafts_succeeded` | Requests with a usable draft. |
| `drafts_skipped` | Requests that were not eligible for a draft. |
| `drafts_failed` | Requests where the local model gave an error, a timeout or an empty draft. |
| `frontier_accepted_count` | Drafts that the frontier model accepted. |
| `frontier_modified_count` | Drafts that the frontier model did not accept. |
| `acceptance_rate_percent` | `frontier_accepted_count` / `drafts_succeeded` × 100. |
| `openai_prediction_injected_count` | Requests sent with the OpenAI `prediction` field. |
| `estimated_output_tokens_saved` | Draft tokens − 2, summed over accepted drafts. |
| `estimated_input_tokens_added` | Tokens of the injected draft text. Estimate: characters / 4. |
| `net_tokens_saved` | `estimated_output_tokens_saved` − `estimated_input_tokens_added`. |
| `estimated_usd_saved` | Value of the saved output tokens, in USD. |
| `estimated_input_usd_cost` | Cost of the added input tokens, in USD. |
| `estimated_net_usd_saved` | `estimated_usd_saved` − `estimated_input_usd_cost`. |
| `avg_local_draft_latency_ms` | Mean draft time. Multi-turn sessions with adaptive context benefit from local prompt cache hits, lowering this average. |
| `p50_local_draft_latency_ms`, `p95_local_draft_latency_ms` | Percentiles of draft time. Useful for tuning `THETARELAY_LOCAL_TIMEOUT`. |
| `avg_frontier_latency_ms` | Mean frontier time. Non-streaming requests only. |
| `p50_end_to_end_latency_ms`, `p95_end_to_end_latency_ms` | Percentiles of draft time + frontier time. Non-streaming requests only. |
| `estimated_net_latency_saved_ms` | Avoided decode time − draft time, summed. Rejected drafts count as negative. |
| `prefix_cache_preserved_count` | Drafted turns where thetarelay did not change the prompt prefix. thetarelay counts this value. It does not read it from the provider. |

thetarelay keeps the metrics in memory. A restart sets them to 0.
