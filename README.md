# ThetaRelay

[![CI](https://github.com/thetaplex/thetarelay/actions/workflows/ci.yml/badge.svg)](https://github.com/thetaplex/thetarelay/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/thetarelay)](https://pypi.org/p/thetarelay)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

ThetaRelay is a speculative drafting proxy. A local model writes a draft of each response. The
frontier model then checks the draft. If the draft is correct, the frontier model writes only
`<ACCEPT/>`, and thetarelay returns the draft.

ThetaRelay works with agents that use the Anthropic Messages API or the OpenAI Chat Completions
API: for example Claude Code, OpenCode, Pi, Cursor and Aider. The local model can be any
OpenAI-compatible server: Ollama, MLX, llama.cpp or vLLM. ThetaRelay runs alone or as an extension
of [Headroom](https://github.com/chopratejas/headroom).

## Why it exists

A frontier model writes output tokens slowly, and output tokens are expensive. Many coding-agent
turns are simple enough for a small local model. When the frontier model accepts a draft, it
writes 2 tokens in place of the full response.

ThetaRelay changes only the end of the last user message. The system prompt and earlier messages
do not change, so the provider prompt cache continues to work.

ThetaRelay also adaptively sizes the local draft context for each conversation
(`THETARELAY_LOCAL_ADAPTIVE_CONTEXT`). It keeps the start of the draft window stable so the local
server reuses its prompt cache, reading only new messages on each turn.

## Install

Prerequisites:

- Python 3.13 or later
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [just](https://just.systems/man/en/packages.html), for a source checkout
- A local model server, for example [Ollama](https://ollama.com/download)

Install the command-line tool:

```sh
uv tool install thetarelay
```

To use ThetaRelay inside Headroom, install it in the Headroom tool environment:

```sh
uv tool install headroom-ai --with thetarelay
```

To install from source:

```sh
git clone https://github.com/thetaplex/thetarelay.git
cd thetarelay
just setup
```

## Quickstart

1. Download the default draft model:

   ```sh
   ollama pull gemma4:e4b
   ```

2. Start the proxy:

   ```sh
   thetarelay proxy
   ```

3. In a new terminal, point the agent at the proxy and start it:

   ```sh
   export ANTHROPIC_BASE_URL=http://127.0.0.1:8788
   claude
   ```

4. Read the statistics:

   ```sh
   curl http://127.0.0.1:8788/thetarelay/stats
   ```

## Usage

| Task | Command |
|---|---|
| Start the proxy | `thetarelay proxy --local-model gemma4:e4b` |
| Run inside Headroom | `headroom proxy --port 8787 --proxy-extension thetarelay` |
| Run the offline benchmark | `thetarelay bench` |
| Evaluate a local model | `thetarelay eval --local-model gemma4:e4b` |
| Run HumanEval | `thetarelay eval --benchmark humaneval --local-model gemma4:e4b` |

In a source checkout, use `just run` in place of `thetarelay`, for example `just run proxy`.

## Results

Measured on 2026-10-30 on an Apple silicon Mac. Draft model: `gemma4:e4b-mlx` (Ollama, MLX).
Frontier models: `anthropic/claude-opus-4.5` (5 USD input / 25 USD output per 10<sup>6</sup> tokens) and `openai/gpt-5.4` (2.50 USD input / 15 USD output per 10<sup>6</sup> tokens) through OpenRouter. Raw data:
[`benchmarks/results/live_benchmark_results_2026_10_30.json`](benchmarks/results/live_benchmark_results_2026_10_30.json).

| Test | Frontier model | Drafts accepted | Frontier output tokens | Cost (USD) | Wall-clock time | Accuracy |
|---|---|---|---|---|---|---|
| HumanEval, 10 problems | `claude-opus-4.5` | 9 of 10 | 4187 → 320 (-92.4%) | 0.1115 → 0.0280 (-74.9%) | 55.6 s → 47.0 s | 10 of 10 pass@1 |
| HumanEval, 10 problems | `openai/gpt-5.4` | 9 of 10 | 1339 → 188 (-86.0%) | 0.0235 → 0.0119 (-49.6%) | 25.2 s → 33.2 s | 10 of 10 pass@1 |
| 15 000-token agent traces with Headroom 0.37.0 | `claude-opus-4.5` | 3 of 3 | 74 → 21 (-71.6%) | 0.2283 → 0.1276 (-44.1%) | 8.1 s → 25.1 s | 3 of 3 valid (direct: 1 of 3) |
| 13 800-token agent traces with Headroom 0.37.0 | `openai/gpt-5.4` | 3 of 3 | 260 → 24 (-90.8%) | 0.0781 → 0.0119 (-84.8%) | 7.45 s → 19.3 s | 3 of 3 match |
| Drafts with injected bugs (HumanEval) and SQL injection | `claude-opus-4.5`, `gpt-5.4`, `qwen3.8:27b-mlx` (local) | 0 of 7 | — | — | — | 6 of 6 HumanEval tasks recovered |

In the agent-trace tests, the wall-clock time increased because the local model read the full
context on each turn. With adaptive drafting enabled (`THETARELAY_LOCAL_ADAPTIVE_CONTEXT`),
ThetaRelay keeps the conversation prefix stable and bounds new read volume to the local timeout:
in a 12-turn replay on Ollama with `gemma4:e4b-mlx`, total draft time fell from 31.6 s to 19.7 s
with zero timeouts, and mean draft time fell from 4.2 s to 2.4 s.
`thetarelay bench` uses simulated values. It does not produce these results.

## Tested models

Confirmed frontier models: `anthropic/claude-opus-4.5` and `openai/gpt-5.4`. Confirmed local
verifier: `qwen3.8:27b-mlx`. All results use the local drafter `gemma4:e4b-mlx`.

Some frontier models accept drafts with bugs. Do not use a frontier model that is not confirmed
before you test it. See [Tested models](docs/reference/tested-models.md).

## Documentation

- [Tutorial: run your first speculative proxy](docs/tutorials/first-speculative-proxy.md)
- [How-to guides](docs/README.md#how-to-guides)
- [Reference: CLI, configuration, HTTP API](docs/README.md#reference)
- [How speculative drafting works](docs/explanation/how-speculative-drafting-works.md)
- [Known limitations](docs/reference/known-limitations.md)

## Links

- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
