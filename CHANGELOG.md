# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

ThetaRelay is now a routing proxy. Speculative drafting is removed.

### Added

- Routing: a local judge (`tev1` on Ollama, or Jev on OpenRouter) sends each request to the local tier (`gemma4:e4b`) or the frontier tier (`openai/gpt-6.1-terra` on OpenRouter).
- A failed tool result keeps the conversation on the frontier tier for `THETARELAY_STAY_REQUESTS` requests.
- Requests go to the frontier tier when they are too large for the local model, when the judge fails, or when the local server fails.
- `thetarelay proxy --debug` and `THETARELAY_DEBUG`: one line for each routing decision.
- `THETARELAY_TURN_LOG`: one JSON line for each request, with the decision, the judge summary, usage and OpenRouter cost.
- `x-thetarelay-tier`, `x-thetarelay-model`, `x-thetarelay-reason` and `x-thetarelay-p-easy` response headers.
- `thetarelay judge`: ask the judge about one step.
- `scripts/judge_eval/`: compare judges on labelled steps.
- `THETARELAY_LITE_API_KEY`: a key for a hosted lite model, for example on OpenRouter.
- `THETARELAY_LITE_TIMEOUT` (default 30 s): a silent lite model falls back to the frontier tier.
- A lite reply that ends the agent's turn goes to the frontier tier, unless the tests just passed.

### Fixed

- OpenAI-format requests to an `anthropic/` frontier model on OpenRouter were never cached: the agent cannot add Anthropic cache markers because it does not know which model the proxy picks. ThetaRelay now adds OpenRouter's top-level `cache_control` marker when the request has none. In a test with Opus 5.5, the second identical 6,000-token request cost $0.0013 instead of $0.030.

### Changed

- Renamed the easy tier from "local" to "lite", because it can also be a hosted model: `THETARELAY_LOCAL_*` is now `THETARELAY_LITE_*` (the old names still work), and the tier, reasons and counters say `lite` (`x-thetarelay-tier: lite`, `too_large_for_lite`, `lite_failed`, `lite_stopped`, `lite_requests`).
- Renamed the project from ThetaCode to ThetaRelay: package `thetarelay`, CLI `thetarelay`, environment variables `THETARELAY_*`, stats endpoint `/thetarelay/stats`.
- Python 3.13 or later is now required (was 3.11).

### Removed

- Moved the paper, the blog post and the output-quality evaluation plan to the `research` branch.

- Speculative drafting: the `accept_swap`, `prediction` and `context_only` strategies, draft injection and `<ACCEPT/>` stream interception. An accepted draft is the local model's answer, not the frontier model's, so it could not be trusted as a quality-preserving method.
- `thetarelay bench` and `thetarelay eval`, the HumanEval harness and the simulated savings metrics.
- The Headroom proxy extension (`headroom proxy --proxy-extension thetarelay`). The embedded Headroom middleware stays.
- Drafting configuration (`THETARELAY_STRATEGY`, `THETARELAY_ACCEPT_TOKEN`, `THETARELAY_LOCAL_TIMEOUT`, `THETARELAY_LOCAL_MAX_TOKENS`, adaptive context settings, price estimates, `THETARELAY_SKIP_MODELS`, `THETARELAY_USECASE`).

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
