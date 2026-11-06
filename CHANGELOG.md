# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-06

First release.

### Added

- `thetarelay proxy`: a routing proxy for coding agents that use the Anthropic Messages API or the OpenAI Chat Completions API.
- Hybrid routing (default): a local judge (`tev1` on Ollama) sends easy steps to the lite model. For the other steps, the frontier model either does the step or hands it to the lite model.
- Defaults tested to reduce cost: Claude Opus 5.5 (frontier) and Claude Haiku 5.5 (lite) on OpenRouter. See [Tested models](docs/reference/tested-models.md).
- Fallbacks to the frontier model: oversized requests, judge errors, lite server errors, and lite replies that end the agent's turn.
- The provider prompt cache of the frontier model keeps working: requests change only in `model`, plus a short triage note after a cache marker.
- `THETARELAY_ROUTE_MODE`: `hybrid`, `judge`, `triage`, and the baselines `frontier`, `lite` and `random`.
- `thetarelay proxy --debug`, the turn log (`THETARELAY_TURN_LOG`), `/stats` and `x-thetarelay-*` response headers.
- `thetarelay judge`: ask the judge about one step.
- Optional Headroom compression middleware (`thetarelay[headroom]`).

[Unreleased]: https://github.com/thetaplex/thetarelay/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/thetaplex/thetarelay/releases/tag/v0.1.0
