# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Documentation in `docs/`: tutorial, how-to guides, reference and explanation pages (Diátaxis, ASD-STE100).

### Changed

- Renamed the project from ThetaCode to ThetaRelay: package `thetarelay`, CLI `thetarelay`, environment variables `THETARELAY_*`, stats endpoint `/thetarelay/stats`, Headroom entry point `thetarelay`.
- The start of the local draft context now moves in steps of half the message limit and half the character budget, not one message per turn. The drafting instructions (`THETARELAY_LOCAL_CONCISE_DRAFT`) are now a separate system message, not an addition to the last user message. Consecutive drafts share a prefix, so the local server can reuse its prompt cache. In a 10-turn replay on Ollama with `gemma4:e4b-mlx`, the mean draft time decreased from 4.2 s to 2.4 s.
- Python 3.13 or later is now required (was 3.11).
- Changed the default of `THETARELAY_LOCAL_TIMEOUT` from `8.0` to `5.0` seconds.
- The proxy and the Headroom extension size the local context for each conversation (`THETARELAY_LOCAL_ADAPTIVE_CONTEXT`, default `true`). The window grows while the new text can be read within the timeout, and restarts from the newest messages when it cannot. `THETARELAY_LOCAL_MAX_CONTEXT_CHARS` is now an upper limit, with the default changed from `24000` to `64000`. In a 12-turn replay on Ollama with `gemma4:e4b-mlx`, the total draft time decreased from 31.6 s (fixed 8000-character window) to 19.7 s, with no timeouts.

### Fixed

- `eval --benchmark humaneval` sent the local drafting instructions ("output only a code block") to the frontier baseline. The baseline now gets the original request, so the measured savings are not understated.
- README: the agent-trace results for `claude-opus-4.5` now match the raw data in `benchmarks/results/live_benchmark_results_2026_10_30.json`.

### Removed

- Moved the paper, the blog post and the output-quality evaluation plan to the `research` branch.

## [0.1.0] - 2026-10-30

First alpha release.

### Added

- Local-to-frontier speculative drafting engine (`thetarelay.engine`) supporting both OpenAI Predicted Outputs (`prediction`) and prefix-cache-preserving `<speculative_draft>` + `<ACCEPT/>` verification for Anthropic Claude and tool calls.
- Zero-latency SSE stream interceptor (`thetarelay.streaming`) that synthesizes full text and `tool_use` SSE events on `<ACCEPT/>` or switches to passthrough on token divergence.
- Native Headroom proxy extension (`headroom.proxy_extension` entry point `thetarelay = "thetarelay.headroom_ext:install"`) with `ThetaRelayTurnHook` and `ThetaRelayStreamingResponseMiddleware`.
- Standalone FastAPI proxy (`thetarelay proxy`) with automatic in-process `headroom.integrations.asgi.CompressionMiddleware` embedding and `/thetarelay/stats` observability endpoint.
- Deterministic A/B benchmark suite (`thetarelay bench` / `just bench`) and live two-phase zero-swap evaluation runner (`thetarelay eval` / `just eval`) including OpenAI HumanEval functional `pass@1` execution (`thetarelay eval --benchmark humaneval`).
- Thinking-mode suppression (`reasoning_effort: "none"`) and concise drafting directives for local reasoning models (e.g., `gemma4`, `qwen3`) to prevent thinking-token latency blowups during speculative drafting.

[Unreleased]: https://github.com/thetaplex/thetarelay/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/thetaplex/thetarelay/releases/tag/v0.1.0
