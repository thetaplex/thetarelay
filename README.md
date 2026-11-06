# ThetaRelay

[![CI](https://github.com/thetaplex/thetarelay/actions/workflows/ci.yml/badge.svg)](https://github.com/thetaplex/thetarelay/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/thetarelay)](https://pypi.org/p/thetarelay)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

ThetaRelay is a routing proxy for coding agents. For each request, a small local judge model
decides if the step is easy. Easy steps go to a lite model. For the other steps, the frontier
model either does the step or hands it to the lite model. The agent does not see the difference:
each response comes from one model, unchanged.

> **Tested models.** ThetaRelay is tested to reduce cost only with Claude Opus 5.5 (frontier)
> and Claude Haiku 5.5 (lite), the defaults. With other models it can cost more than no routing.
> See [Tested models](docs/reference/tested-models.md).

ThetaRelay works with agents that use the Anthropic Messages API or the OpenAI Chat Completions
API, for example Claude Code, OpenCode, Pi, Cursor and Aider.

## Why it exists

Most requests in a coding-agent session are routine: read a file, search, list, run the tests.
A frontier model is expensive for these steps. ThetaRelay sends them to a cheap lite model and
keeps the frontier model for debugging, planning and difficult edits. The frontier model also
decides when the work is done.

ThetaRelay keeps the provider prompt cache of the frontier model working: it changes only the
`model` field, plus a short note at the end of requests that the frontier model triages.

## Install

Prerequisites:

- Python 3.13 or later
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [just](https://just.systems/man/en/packages.html), for a source checkout
- [Ollama](https://ollama.com/download) 0.35 or later, for the judge
- An [OpenRouter](https://openrouter.ai) API key, for the frontier and lite models

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

1. Download the judge:

   ```sh
   ollama pull tev1
   ```

2. Start the proxy. Both tiers use your OpenRouter key:

   ```sh
   export OPENROUTER_API_KEY=sk-or-...
   export THETARELAY_LITE_API_KEY=$OPENROUTER_API_KEY
   thetarelay proxy --debug
   ```

3. Point the agent at the proxy, for example Claude Code:

   ```sh
   ANTHROPIC_BASE_URL=http://127.0.0.1:8788 claude
   ```

With `--debug`, ThetaRelay prints one line for each request:

```text
[thetarelay] conv 33723461a51d msg 1  P(easy)=0.99  judge → lite (anthropic/claude-haiku-5.5)  status 200  1689 ms
[thetarelay] conv 1af65bd07298 msg 7  P(easy)=0.41  judge; triage → frontier (anthropic/claude-opus-5.5)  status 200  21734 ms
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
- [Tested models](docs/reference/tested-models.md)
- [Known limitations](docs/reference/known-limitations.md)

## Links

- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
