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
| `THETARELAY_LOCAL_MODEL` | `qwen2.5-coder:7b` | Model name sent to the local API. |
| `THETARELAY_LOCAL_API_KEY` | `local` | Bearer token sent to the local API. |
| `THETARELAY_LOCAL_TIMEOUT` | `5.0` | Time limit of one draft request, in seconds. |
| `THETARELAY_LOCAL_MAX_TOKENS` | `384` | Maximum draft length, in tokens. |
| `THETARELAY_LOCAL_TEMPERATURE` | `0.0` | Sampling temperature of the draft. |
| `THETARELAY_LOCAL_DISABLE_THINKING` | `true` | Sends `reasoning_effort: "none"` to the local API. |
| `THETARELAY_LOCAL_CONCISE_DRAFT` | `true` | Adds a system message to the local request: write code only, with no explanation. |
| `THETARELAY_LOCAL_MAX_CONTEXT_CHARS` | `64000` | Maximum characters of conversation sent to the local model. The system prompt gets one third of this value. With `THETARELAY_LOCAL_ADAPTIVE_CONTEXT=true`, this is an upper limit only. Otherwise, the start of the window moves in steps of half this value; use approximately `8000` in that case. |
| `THETARELAY_LOCAL_ADAPTIVE_CONTEXT` | `true` | Sizes the local context for each conversation. The window keeps its start and grows while the new text can be read within `THETARELAY_LOCAL_TIMEOUT`. If the new text is too large, the window restarts from the newest messages that can be read in time. thetarelay measures the read speed of the local server on each draft. Applies to `thetarelay proxy` and the Headroom extension, not to `eval` or `bench`. |
| `THETARELAY_LOCAL_RECENT_MESSAGES` | `10` | Maximum number of recent messages sent to the local model when `THETARELAY_LOCAL_ADAPTIVE_CONTEXT=false`. The start of the window moves in steps of half this value, so the local model gets between 6 and 10 messages. |

## Drafting behaviour

| Variable | Default | Description |
|---|---|---|
| `THETARELAY_STRATEGY` | `auto` | `auto`, `accept_swap`, `prediction` or `context_only`. See [How speculative drafting works](../explanation/how-speculative-drafting-works.md). |
| `THETARELAY_SELECTIVE` | `true` | If true, thetarelay does not draft on short prompts. |
| `THETARELAY_MIN_PROMPT_CHARS` | `15` | Minimum length of the last message for a draft, in characters. |
| `THETARELAY_SKIP_MODELS` | `haiku,mini,nano,flash` | Comma-separated words. thetarelay does not draft if the model name contains one of them. |
| `THETARELAY_ACCEPT_TOKEN` | `<ACCEPT/>` | Text that the frontier model writes to accept the draft. |

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
