# Reference: configuration

thetarelay reads these environment variables when it starts. Source: `src/thetarelay/config.py`.

Boolean values: `1`, `true`, `yes` and `on` are true. All other values are false. The values are
not case-sensitive.

If a number is not valid, thetarelay uses the default. If a strategy is not valid, thetarelay does
not show an error. See [Known limitations](known-limitations.md).

## Server and upstream

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_HOST` | `127.0.0.1` | Bind address of `thetarelay proxy`. |
| `THETARELAY_PORT` | `8788` | Bind port of `thetarelay proxy`. |
| `THETARELAY_ANTHROPIC_UPSTREAM` | `https://api.anthropic.com` | Anthropic base URL. thetarelay adds `/v1/messages`, or `/messages` if the URL ends with `/v1`. |
| `THETARELAY_OPENAI_UPSTREAM` | `https://api.openai.com` | OpenAI base URL. thetarelay adds `/v1/chat/completions`, or `/chat/completions` if the URL ends with `/v1`. |
| `THETARELAY_HEADROOM_MIDDLEWARE` | `true` | Adds Headroom `CompressionMiddleware` if `headroom-ai` is installed. |

## Local draft model

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_LOCAL_BASE_URL` | `http://127.0.0.1:11434/v1` | Base URL of an OpenAI-compatible API: Ollama, MLX, llama.cpp or vLLM. |
| `THETARELAY_LOCAL_MODEL` | `gemma4:e4b` | Model name sent to the local API. |
| `THETARELAY_LOCAL_API_KEY` | `local` | Bearer token sent to the local API. |
| `THETARELAY_LOCAL_TIMEOUT` | `5.0` | Time limit of one draft request, in seconds. With `THETARELAY_LOCAL_ADAPTIVE_CONTEXT=true`, 80 % of this limit is allocated to reading new prompt characters and 20 % to token decoding. |
| `THETARELAY_LOCAL_MAX_TOKENS` | `384` | Maximum draft length, in tokens. |
| `THETARELAY_LOCAL_TEMPERATURE` | `0.0` | Sampling temperature of the draft. |
| `THETARELAY_LOCAL_DISABLE_THINKING` | `true` | Sends `reasoning_effort: "none"` to the local API. |
| `THETARELAY_LOCAL_CONCISE_DRAFT` | `true` | Adds a separate system message to the local request with the drafting instructions of `THETARELAY_USECASE`. Keeping instructions in an independent system message preserves the local server's prompt cache across turns. |
| `THETARELAY_LOCAL_MAX_CONTEXT_CHARS` | `64000` | Maximum characters of conversation sent to the local model. The system prompt gets up to one third of this value. With `THETARELAY_LOCAL_ADAPTIVE_CONTEXT=true`, this is an upper ceiling. If disabled, the start of the window moves in steps of half this value; use approximately `8000` in that case. |
| `THETARELAY_LOCAL_ADAPTIVE_CONTEXT` | `true` | Dynamically sizes the local draft window for each conversation. The window keeps its start and grows while new text fits within the read budget of `THETARELAY_LOCAL_TIMEOUT` and total characters fit within `THETARELAY_LOCAL_MAX_CONTEXT_CHARS`. When new text is too large or the ceiling is reached, the window restarts from the newest messages that can be read in time. thetarelay tracks up to 64 active conversations in memory and measures local read speed on each draft. Applies to `thetarelay proxy` and the Headroom extension, not to `eval` or `bench`. |
| `THETARELAY_LOCAL_RECENT_MESSAGES` | `10` | Maximum number of recent messages sent to the local model when `THETARELAY_LOCAL_ADAPTIVE_CONTEXT=false`. The start of the window moves in steps of half this value, so the local model gets between 6 and 10 messages. |

## Drafting behaviour

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_STRATEGY` | `auto` | `auto`, `accept_swap`, `prediction` or `context_only`. See [How speculative drafting works](../explanation/how-speculative-drafting-works.md). |
| `THETARELAY_USECASE` | `coding` | Kind of agent that thetarelay serves. Selects the drafting instructions sent to the local model. Only `coding` is available. If the value is not known, thetarelay stops with an error when it starts. |
| `THETARELAY_SELECTIVE` | `true` | If true, thetarelay does not draft on short prompts. |
| `THETARELAY_MIN_PROMPT_CHARS` | `15` | Minimum length of the last message for a draft, in characters. |
| `THETARELAY_SKIP_MODELS` | `haiku,mini,nano,flash` | Comma-separated words. thetarelay does not draft if the model name contains one of them. |
| `THETARELAY_ACCEPT_TOKEN` | `<ACCEPT/>` | Text that the frontier model writes to accept the draft. |

## Turn log

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_TURN_LOG` | Empty (off) | Path of a JSON Lines file. The proxy appends one line for each request it forwards. Use it to review accepted drafts after an evaluation run. The file contains prompt and draft text, so keep it private. It never contains request headers or API keys. |

Fields of each line:

| Field | Value |
|---|---|
| `ts` | Time the turn finished, ISO 8601, UTC. |
| `provider` | `anthropic` or `openai`. |
| `model` | Model name of the request. |
| `conversation` | First 12 hex characters of the SHA-256 of the first user message. Turns of one agent session have the same value. |
| `turn` | Number of messages in the request. |
| `stream` | `true` if the request was streamed. |
| `mode` | `accept_swap`, `openai_prediction`, `context_only` or `none`. |
| `drafted`, `skip_reason`, `draft_error` | If a draft was made, and if not, why. |
| `draft_ms`, `frontier_ms` | Draft time and frontier time in milliseconds. `frontier_ms` is `null` for streamed turns. |
| `accepted` | `true` if the client received the draft, `false` if the frontier model rejected it, `null` if no `<ACCEPT/>` decision was made. |
| `draft_text`, `draft_tool_calls` | The local draft. |
| `last_message` | Text of the last message of the request. |

## Estimates for statistics

These values affect the statistics only. They do not change the requests.

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_INPUT_USD_PER_M` | `3.0` | Input token price, in USD per 1 000 000 tokens. |
| `THETARELAY_OUTPUT_USD_PER_M` | `15.0` | Output token price, in USD per 1 000 000 tokens. |
| `THETARELAY_FRONTIER_OUTPUT_TPS` | `65.0` | Frontier output speed, in tokens per second. |

## Evaluation only

| Variable | Description |
|---|---|
| `THETARELAY_TARGET_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY` | `eval --benchmark humaneval` sends the first variable that has a value to the target model as a bearer token. If none has a value, it sends `THETARELAY_LOCAL_API_KEY`. `eval --benchmark scenarios` does not read these variables. See [Known limitations](known-limitations.md). |
