# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The turn log records the step groups of each reply (`step_groups`), cache-write tokens and the triage verdict.
- `THETARELAY_LITE_GUARD` (`off`, `irreversible`, `writes`): discards a lite reply with a malformed, irreversible or (at `writes`) file-editing tool call and asks the frontier tier instead. New reasons `lite_malformed`, `lite_irreversible` and `lite_wrote`, and the turn-log field `guarded_step_groups`. The turn-log fields `lite_output_chars` and `discarded_lite_cost_usd` record the size and cost of each discarded lite reply, and the evaluation reports count that cost.
- `THETARELAY_SHADOW_SHARE`: send a share of frontier steps to the lite tier as well, never shown to the agent, and log both replies' tool calls (`shadow`) in the turn log. The judge is asked in every route mode when the share is above 0.

### Changed

- Evaluation harnesses (`scripts/routing_eval`, `scripts/app_eval`): matched random arms for `judge`, `triage` and `hybrid`, ablation arms, three app specs, and reports with confidence intervals and paired differences. See [Tested models](docs/reference/tested-models.md#release-run).

### Fixed

- Reading a file that mentions errors no longer counts as a failed step, so the conversation can stay on the lite model.
- Pi commands that exit non-zero (`Command exited with code N`) are now caught as failed steps.

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
