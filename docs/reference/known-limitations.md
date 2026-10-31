# Reference: known limitations

This list applies to version 0.1.0 and the `feat/speculative-proxy` branch. Last review:
2026-10-30.

## Security

| Limitation | Effect |
|---|---|
| The proxy has no authentication. | If you set `--host` to an address other than `127.0.0.1`, other computers can use your local model and your proxy. |
| `eval --benchmark humaneval` runs model code with `exec()` in a child process. | The only protection is a 3-second time limit. The code can read and change files and can use the network. |

## Drafting

| Limitation | Effect |
|---|---|
| The `prediction` strategy works only for OpenAI text drafts. | For Anthropic requests and for tool-call drafts, thetarelay adds the `<ACCEPT/>` instruction but does not swap the response. The client can receive the text `<ACCEPT/>`. Use `auto` in place of `prediction`. |
| thetarelay does not validate `THETARELAY_STRATEGY`. | A value that is not valid causes the same problem as `prediction`. |
| The Headroom extension calls the local model synchronously. | Each drafted turn can stop the Headroom process for up to `THETARELAY_LOCAL_TIMEOUT` seconds. |
| The Headroom extension reads environment variables only. | It does not use the configuration that Headroom gives to it. |
| The adaptive context keeps its state in memory. | After the proxy restarts, the first draft of each conversation reads the full window. |
| The adaptive context does not include the system prompt in its time estimate. | The system prompt can use up to one third of `THETARELAY_LOCAL_MAX_CONTEXT_CHARS`. On the first draft of a conversation, a long system prompt can make the draft exceed `THETARELAY_LOCAL_TIMEOUT`. |
| The adaptive context removes old messages, not the start of a large message. | If one tool result is larger than the window, the local model sees only its end. For a large diff, this can remove the file names. |
| Token counts are estimates. | thetarelay uses characters / 4 when the local API does not return `usage`. |

## Metrics and benchmarks

| Limitation | Effect |
|---|---|
| `bench` uses simulated latencies, token counts and frontier decisions. | Its speedup and cost values show the method. They are not measurements. |
| `bench` always reports `false_accepts: 0`. | The value is fixed in the code. It is not measured. |
| `prefix_cache_preserved_count` is counted, not measured. | thetarelay does not read the cache usage from the provider. |
| Streaming requests do not record frontier time. | `avg_frontier_latency_ms` and the end-to-end percentiles include non-streaming requests only. |
| `openai_prediction` turns do not record accepted or rejected outcomes. | `acceptance_rate_percent` does not include them. |

## Command-line interface

| Limitation | Effect |
|---|---|
| `thetarelay` without a command does not accept options. | Use `thetarelay proxy --port 9000`, not `thetarelay --port 9000`. |
| `--port 0` has no effect. | thetarelay uses the default port. |
| `eval --json` has no effect. | `eval` always writes JSON. |
| `eval` supports OpenAI-compatible targets only. | You cannot use the native Anthropic `/v1/messages` API as the target in `eval`; use an OpenAI-compatible endpoint such as OpenRouter. |
