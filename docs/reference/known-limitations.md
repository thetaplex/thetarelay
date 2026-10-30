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
| The `prediction` strategy works only for OpenAI text drafts. | For Anthropic requests and for tool-call drafts, thetacode adds the `<ACCEPT/>` instruction but does not swap the response. The client can receive the text `<ACCEPT/>`. Use `auto` in place of `prediction`. |
| thetacode does not validate `THETACODE_STRATEGY`. | A value that is not valid causes the same problem as `prediction`. |
| The Headroom extension calls the local model synchronously. | Each drafted turn can stop the Headroom process for up to `THETACODE_LOCAL_TIMEOUT` seconds. |
| The Headroom extension reads environment variables only. | It does not use the configuration that Headroom gives to it. |
| Token counts are estimates. | thetacode uses characters / 4 when the local API does not return `usage`. |

## Metrics and benchmarks

| Limitation | Effect |
|---|---|
| `bench` uses simulated latencies, token counts and frontier decisions. | Its speedup and cost values show the method. They are not measurements. |
| `bench` always reports `false_accepts: 0`. | The value is fixed in the code. It is not measured. |
| `prefix_cache_preserved_count` is counted, not measured. | thetacode does not read the cache usage from the provider. |
| Streaming requests do not record frontier time. | `avg_frontier_latency_ms` and the end-to-end percentiles include non-streaming requests only. |
| `openai_prediction` turns do not record accepted or rejected outcomes. | `acceptance_rate_percent` does not include them. |

## Command-line interface

| Limitation | Effect |
|---|---|
| `thetacode` without a command does not accept options. | Use `thetacode proxy --port 9000`, not `thetacode --port 9000`. |
| `--port 0` has no effect. | thetacode uses the default port. |
| `eval --json` has no effect. | `eval` always writes JSON. |
| In `eval --benchmark scenarios`, the target model receives `THETACODE_LOCAL_API_KEY`. | A hosted target model rejects the request unless `THETACODE_LOCAL_API_KEY` holds its key. `eval --benchmark humaneval` sends the target key correctly. |
| `eval` supports OpenAI-compatible targets only. | You cannot use the Anthropic API as the target in `eval`. |
