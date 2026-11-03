# thetarelay

Open-source routing proxy (PyPI) for coding agents (Pi, OpenCode, Cursor, Claude Code). A local judge model (tev1 on Ollama) sends easy requests to a local model and hard requests to a frontier model on OpenRouter. Part of the ThetaPlex organisation.

## Stack
Python 3.13+, uv, hatchling, FastAPI, uvicorn, httpx. Optional extra: `headroom` (`headroom-ai`). Dev tools live in the `dev` dependency group. Apache-2.0.

## Commands
- `just setup` - install from the lockfile
- `just check` - lint, format check, mypy, tests (what CI runs)
- `just fmt` - format and apply safe lint fixes
- `just run proxy --help` - inspect proxy CLI options

## Rules
- Tests never hit real provider APIs; mock with `httpx.MockTransport`.
- Never print or log secrets or API keys.
- Preserve Anthropic and OpenAI prefix caches: forward request bodies unchanged except `model` (thinking off for the lite tier; a top-level `cache_control` for OpenAI-format requests to `anthropic/` frontier models). Never edit the system prompt, messages or tools.
- Never send the client's cloud credentials to the lite tier; it gets only `THETARELAY_LITE_API_KEY`.
- Uncertain routing goes to the frontier tier (judge error, recent failure, oversized request).
