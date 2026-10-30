# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-30

First alpha release.

### Added

- Local-to-frontier speculative drafting engine (`thetacode.engine`) supporting both OpenAI Predicted Outputs (`prediction`) and prefix-cache-preserving `<speculative_draft>` + `<ACCEPT/>` verification for Anthropic Claude and tool calls.
- Zero-latency SSE stream interceptor (`thetacode.streaming`) that synthesizes full text and `tool_use` SSE events on `<ACCEPT/>` or switches to passthrough on token divergence.
- Native Headroom proxy extension (`headroom.proxy_extension` entry point `thetacode = "thetacode.headroom_ext:install"`) with `ThetaCodeTurnHook` and `ThetaCodeStreamingResponseMiddleware`.
- Standalone FastAPI proxy (`thetacode proxy`) with automatic in-process `headroom.integrations.asgi.CompressionMiddleware` embedding and `/thetacode/stats` observability endpoint.

[Unreleased]: https://github.com/thetaplex/thetacode/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/thetaplex/thetacode/releases/tag/v0.1.0
