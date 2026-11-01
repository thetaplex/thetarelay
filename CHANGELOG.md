# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-31

First alpha release. Requires Python 3.13 or later.

### Added

- Local-to-frontier speculative drafting engine (`thetarelay.engine`) supporting both OpenAI Predicted Outputs (`prediction`) and prefix-cache-preserving `<speculative_draft>` + `<ACCEPT/>` verification for Anthropic Claude and tool calls.
- Zero-latency SSE stream interceptor (`thetarelay.streaming`) that synthesizes full text and `tool_use` SSE events on `<ACCEPT/>` or switches to passthrough on token divergence.
- Native Headroom proxy extension (`headroom.proxy_extension` entry point `thetarelay = "thetarelay.headroom_ext:install"`) with `ThetaRelayTurnHook` and `ThetaRelayStreamingResponseMiddleware`.
- Standalone FastAPI proxy (`thetarelay proxy`) with automatic in-process `headroom.integrations.asgi.CompressionMiddleware` embedding and `/thetarelay/stats` observability endpoint.
- Adaptive local draft context (`THETARELAY_LOCAL_ADAPTIVE_CONTEXT`, default `true`) in the proxy and the Headroom extension. The window grows while the new text can be read within `THETARELAY_LOCAL_TIMEOUT` (default `5.0` seconds), and restarts from the newest messages when it cannot. `THETARELAY_LOCAL_MAX_CONTEXT_CHARS` (default `64000`) is an upper limit. The start of the window moves in steps of half the message limit and half the character budget, and the drafting instructions (`THETARELAY_LOCAL_CONCISE_DRAFT`) are a separate system message, so consecutive drafts share a prefix and the local server can reuse its prompt cache. In a 12-turn replay on Ollama with `gemma4:e4b-mlx`, the total draft time decreased from 31.6 s (fixed 8000-character window) to 19.7 s, with no timeouts.
- Default local drafter `gemma4:e4b` (`THETARELAY_LOCAL_MODEL`), the cross-platform build of the drafter used for the published results.
- Deterministic A/B benchmark suite (`thetarelay bench` / `just bench`) and live two-phase zero-swap evaluation runner (`thetarelay eval` / `just eval`) including OpenAI HumanEval functional `pass@1` execution (`thetarelay eval --benchmark humaneval`). A local draft that exceeds `THETARELAY_LOCAL_TIMEOUT` counts as no draft, and the frontier baseline gets the original request without the local drafting instructions. With `--target-model`, the target key comes from `THETARELAY_TARGET_API_KEY`, `OPENROUTER_API_KEY` or `OPENAI_API_KEY`; `THETARELAY_LOCAL_API_KEY` is used only when the target is the local server, and `eval` stops with an error otherwise.
- Thinking-mode suppression (`reasoning_effort: "none"`) and concise drafting directives for local reasoning models (e.g., `gemma4`, `qwen3`) to prevent thinking-token latency blowups during speculative drafting.
- Live benchmark results in `benchmarks/results/live_benchmark_results_2026_10_30.json`.
- Documentation in `docs/`: tutorial, how-to guides, reference and explanation pages (Diátaxis, ASD-STE100), including `docs/reference/tested-models.md` with the frontier and local models that were tested, and which are confirmed.

[Unreleased]: https://github.com/thetaplex/thetarelay/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/thetaplex/thetarelay/releases/tag/v0.1.0
