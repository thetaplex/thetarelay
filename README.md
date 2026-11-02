# ThetaRelay

[![CI](https://github.com/thetaplex/thetarelay/actions/workflows/ci.yml/badge.svg)](https://github.com/thetaplex/thetarelay/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/thetarelay)](https://pypi.org/p/thetarelay)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

ThetaRelay is a routing proxy for coding agents. For each request, a small local judge model
decides if the step is easy or hard. Easy steps go to a local model. Hard steps go to a frontier
model. The agent does not see the difference: each response comes from one model, unchanged.

ThetaRelay works with agents that use the Anthropic Messages API or the OpenAI Chat Completions
API, for example Claude Code, OpenCode, Pi, Cursor and Aider.

## Why it exists

Most requests in a coding-agent session are routine: read a file, search, list, run the tests.
A frontier model is expensive for these steps. ThetaRelay sends them to a free local model and
keeps the frontier model for debugging, planning and difficult edits.

After a tool result that shows a failure (for example a failed test), ThetaRelay keeps the
conversation on the frontier model for the next requests. ThetaRelay changes only the `model`
field of a request, so the provider prompt cache of the frontier model continues to work.

## Install

Prerequisites:

- Python 3.13 or later
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [just](https://just.systems/man/en/packages.html), for a source checkout
- [Ollama](https://ollama.com/download) 0.35 or later (decision-model API)
- An [OpenRouter](https://openrouter.ai) API key for the frontier model

```sh
uv tool install thetarelay
```

To install from source:

```sh
git clone https://github.com/thetaplex/thetarelay.git
cd thetarelay
just setup
```

## Quickstart

1. Download the judge and the local model:

   ```sh
   ollama pull tev1
   ollama pull gemma4:e4b
   ```

2. Start the proxy:

   ```sh
   export OPENROUTER_API_KEY=sk-or-...
   thetarelay proxy --debug
   ```

3. Point the agent at the proxy, for example Claude Code:

   ```sh
   ANTHROPIC_BASE_URL=http://127.0.0.1:8788 claude
   ```

With `--debug`, ThetaRelay prints one line for each request:

```text
[thetarelay] conv 33723461a51d msg 1  P(easy)=0.99  judge → local (gemma4:e4b)  status 200  4689 ms
[thetarelay] conv 1af65bd07298 msg 1  P(easy)=0.21  judge → frontier (openai/gpt-6.1-terra)  status 200  2884 ms
```

## Usage

| Task | Command |
|---|---|
| Start the proxy | `thetarelay proxy` |
| Print every routing decision | `thetarelay proxy --debug` |
| Write a JSON line for each request | `THETARELAY_TURN_LOG=turns.jsonl thetarelay proxy` |
| Ask the judge about one step | `thetarelay judge "[tool bash] FAILED test_dst"` |
| Show request counts per tier | `curl http://127.0.0.1:8788/stats` |

In a source checkout, use `just run` in place of `thetarelay`, for example `just run proxy`.

## Documentation

- [How routing works](docs/explanation/how-routing-works.md)
- [Connect a coding agent](docs/how-to/connect-coding-agents.md)
- [Reference: CLI, configuration, HTTP API](docs/README.md#reference)
- [Known limitations](docs/reference/known-limitations.md)

## Links

- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
