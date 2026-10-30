# Reference: configuration

thetacode reads these environment variables when it starts. Source: `src/thetacode/config.py`.

Boolean values: `1`, `true`, `yes` and `on` are true. All other values are false. The values are
not case-sensitive.

If a number is not valid, thetacode uses the default. If a strategy is not valid, thetacode does
not show an error. See [Known limitations](known-limitations.md).

## Server and upstream

| Variable | Default | Description |
|---|---|---|
| `THETACODE_HOST` | `127.0.0.1` | Bind address of `thetacode proxy`. |
| `THETACODE_PORT` | `8788` | Bind port of `thetacode proxy`. |
| `THETACODE_ANTHROPIC_UPSTREAM` | `https://api.anthropic.com` | Anthropic base URL. thetacode adds `/v1/messages`, or `/messages` if the URL ends with `/v1`. |
| `THETACODE_OPENAI_UPSTREAM` | `https://api.openai.com` | OpenAI base URL. thetacode adds `/v1/chat/completions`, or `/chat/completions` if the URL ends with `/v1`. |
| `THETACODE_HEADROOM_MIDDLEWARE` | `true` | Adds Headroom `CompressionMiddleware` if `headroom-ai` is installed. |

## Local draft model

| Variable | Default | Description |
|---|---|---|
| `THETACODE_LOCAL_BASE_URL` | `http://127.0.0.1:11434/v1` | Base URL of an OpenAI-compatible API: Ollama, MLX, llama.cpp or vLLM. |
| `THETACODE_LOCAL_MODEL` | `qwen2.5-coder:7b` | Model name sent to the local API. |
| `THETACODE_LOCAL_API_KEY` | `local` | Bearer token sent to the local API. |
| `THETACODE_LOCAL_TIMEOUT` | `8.0` | Time limit of one draft request, in seconds. |
| `THETACODE_LOCAL_MAX_TOKENS` | `384` | Maximum draft length, in tokens. |
| `THETACODE_LOCAL_TEMPERATURE` | `0.0` | Sampling temperature of the draft. |
| `THETACODE_LOCAL_DISABLE_THINKING` | `true` | Sends `reasoning_effort: "none"` to the local API. |
| `THETACODE_LOCAL_CONCISE_DRAFT` | `true` | Adds an instruction to the last user message: write code only, with no explanation. |
| `THETACODE_LOCAL_MAX_CONTEXT_CHARS` | `24000` | Maximum characters of conversation sent to the local model. The system prompt gets one third of this value. |
| `THETACODE_LOCAL_RECENT_MESSAGES` | `10` | Maximum number of recent messages sent to the local model. |

## Drafting behaviour

| Variable | Default | Description |
|---|---|---|
| `THETACODE_STRATEGY` | `auto` | `auto`, `accept_swap`, `prediction` or `context_only`. See [How speculative drafting works](../explanation/how-speculative-drafting-works.md). |
| `THETACODE_SELECTIVE` | `true` | If true, thetacode does not draft on short prompts. |
| `THETACODE_MIN_PROMPT_CHARS` | `15` | Minimum length of the last message for a draft, in characters. |
| `THETACODE_SKIP_MODELS` | `haiku,mini,nano,flash` | Comma-separated words. thetacode does not draft if the model name contains one of them. |
| `THETACODE_ACCEPT_TOKEN` | `<ACCEPT/>` | Text that the frontier model writes to accept the draft. |

## Estimates for statistics

These values affect the statistics only. They do not change the requests.

| Variable | Default | Description |
|---|---|---|
| `THETACODE_INPUT_USD_PER_M` | `3.0` | Input token price, in USD per 1 000 000 tokens. |
| `THETACODE_OUTPUT_USD_PER_M` | `15.0` | Output token price, in USD per 1 000 000 tokens. |
| `THETACODE_FRONTIER_OUTPUT_TPS` | `65.0` | Frontier output speed, in tokens per second. |

## Evaluation only

| Variable | Description |
|---|---|
| `THETACODE_TARGET_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY` | `eval --benchmark humaneval` sends the first variable that has a value to the target model as a bearer token. If none has a value, it sends `THETACODE_LOCAL_API_KEY`. `eval --benchmark scenarios` does not read these variables. See [Known limitations](known-limitations.md). |
