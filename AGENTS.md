# thetacode

Open-source CLI and Headroom proxy extension (PyPI) that performs local-to-frontier speculative drafting for coding agents (Pi, OpenCode, Cursor, Claude Code). Part of the ThetaPlex organisation.

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
- Preserve Anthropic and OpenAI prefix caches: never mutate the system prompt or historical messages when injecting speculative drafts; append only to the final user turn.
