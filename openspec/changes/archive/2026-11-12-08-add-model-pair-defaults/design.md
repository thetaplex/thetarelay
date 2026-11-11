# Design: model pair defaults

## Context

`RouterConfig.route_mode` defaults to `"hybrid"` and is read from `THETARELAY_ROUTE_MODE` in
`from_env()`. `cli.py` prints the models and URLs at start-up but not the route mode.
`docs/reference/tested-models.md` reports no saving with OpenAI frontier models: they do not
answer `<EASY/>` to the triage note, so in `hybrid` mode the note only adds cost.

## Decisions

- **Resolve in `from_env()`.** If `THETARELAY_ROUTE_MODE` is unset or empty, the mode is `judge`
  when `frontier_model` starts with `openai/` (case-sensitive, as OpenRouter writes it) and
  `hybrid` otherwise. The dataclass default stays `"hybrid"`, so code that builds a
  `RouterConfig` directly is unchanged.
- **Record why.** `RouterConfig` gains `route_mode_reason: str` (`"set"`, `"default"` or
  `"openai frontier model"`), set by `from_env()`; not an environment variable.
- **Start-up line.** `cli.py` prints `  route mode: <mode>` and, for the OpenAI case, `(OpenAI
  frontier models do not hand steps to the lite tier, so the triage step is off)`.
- **Prefix rule, no table.** One family behaves differently today; a table of exact names
  would go stale with every release. Add exact names only if a model in a family differs.
- **No new saving claim.** `judge` mode with an OpenAI frontier model is not measured. The docs
  say the default avoids the triage cost; they do not claim a saving.
- **Out of scope:** the tested-pairs table row for Opus 5.5 with a local Qwen lite model. It
  needs a paid evaluation run, which is the user's to start.

## Risks / Trade-offs

- [Behaviour change for users with an OpenAI frontier model and no route mode] → Changelog
  entry under `Changed`; the start-up line shows the mode and the reason.
