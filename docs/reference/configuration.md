# Configuration

ThetaRelay reads its configuration from environment variables when it starts.

## Server

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_HOST` | `127.0.0.1` | Bind address. |
| `THETARELAY_PORT` | `8788` | Bind port. |
| `THETARELAY_HEADROOM_MIDDLEWARE` | `true` | Loads the Headroom `CompressionMiddleware` if `headroom-ai` is installed. |

## Lite tier

The lite tier takes the easy requests. It can be a local server (Ollama) or a hosted one
(OpenRouter). The pre-0.2 names `THETARELAY_LOCAL_*` still work; the `THETARELAY_LITE_*` name wins
if both are set.

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_LITE_BASE_URL` | `http://127.0.0.1:11434` | Base URL without `/v1`. The server must have the Anthropic Messages and OpenAI Chat Completions APIs. Examples: Ollama, `https://openrouter.ai/api`. |
| `THETARELAY_LITE_MODEL` | `gemma4:e4b` | Model for easy requests. |
| `THETARELAY_LITE_API_KEY` | Empty | Key sent as `Authorization: Bearer` to the lite tier. Empty: ThetaRelay sends no key. It never sends the key of the agent to the lite tier. |
| `THETARELAY_LITE_DISABLE_THINKING` | `true` | Sends `thinking: {"type": "disabled"}` (Anthropic) or `reasoning_effort: "none"` (OpenAI) to the lite tier. |
| `THETARELAY_LITE_MAX_REQUEST_CHARS` | `100000` | Requests larger than this number of characters go to the frontier tier. |
| `THETARELAY_LITE_TIMEOUT` | `30.0` | If the lite tier sends nothing for this number of seconds, the request goes to the frontier tier. |
| `THETARELAY_ESCALATE_ON_STOP` | `true` | If a lite response has no tool call (so it would end the agent's turn) and the tests did not just pass, discard it and send the request to the frontier tier. |

## Frontier tier

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_FRONTIER_BASE_URL` | `https://openrouter.ai/api` | Base URL without `/v1`. The server must have both APIs. |
| `THETARELAY_FRONTIER_MODEL` | `openai/gpt-6.1-terra` | Model for hard requests. |
| `THETARELAY_FRONTIER_API_KEY` | Empty | Key sent as `Authorization: Bearer`. If empty, ThetaRelay uses `OPENROUTER_API_KEY`. If both are empty, it sends the key of the agent. |

## Judge

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_JUDGE_URL` | `http://127.0.0.1:11434/v1/systemone` | Decision API endpoint. For Jev on OpenRouter, use `https://openrouter.ai/api/alpha/decisions`. |
| `THETARELAY_JUDGE_MODEL` | `tev1` | Judge model. For Jev on OpenRouter, use `typesafe/jev-1.14`. |
| `THETARELAY_JUDGE_API_KEY` | Empty | Key for the judge endpoint. Ollama does not need one. |
| `THETARELAY_JUDGE_TIMEOUT` | `10.0` | Time limit of one judge request, in seconds. If it expires, the request goes to the frontier tier. |
| `THETARELAY_EASY_THRESHOLD` | `0.7` | A request goes to the lite tier only if the judge gives P(easy) above this value. Must be between 0 and 1. For `tev1:0.8b`, use approximately `0.5`. |
| `THETARELAY_STAY_REQUESTS` | `6` | After a failed tool result, this number of agent requests go to the frontier tier without the judge. |

## Logs

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_DEBUG` | `false` | Prints one line to standard error for each request. |
| `THETARELAY_TURN_LOG` | Empty (off) | Path of a JSON Lines file. ThetaRelay appends one line for each request. See [HTTP API](http-api.md#turn-log). |
