# Tested models

ThetaRelay is tested to reduce cost with one setup: **Claude Opus 5.5** as the frontier model
and **Claude Haiku 5.5** as the lite model, both on OpenRouter. These are the defaults. In our
test, this setup cost 32% less than Opus alone, with the same results.

With other models, ThetaRelay can cost more than no routing. Measure before you change models.

## Use the tested setup

Keep the default settings and give both tiers your OpenRouter key:

```sh
export OPENROUTER_API_KEY=sk-or-...
export THETARELAY_LITE_API_KEY=$OPENROUTER_API_KEY
thetarelay proxy
```

## Other models

| Frontier model | Result |
|---|---|
| OpenAI (GPT-6.1 Terra, GPT-6 Astra) | No saving. These models do not hand steps to the lite model, so the default route mode is `judge`, which avoids the triage cost. |
| Low-cost models such as GPT-6.1 Terra | Little to save. Use the frontier model without ThetaRelay. |

Small local lite models, for example `gemma4:e4b`, often stop the agent. ThetaRelay then asks
the frontier model again, so they add time and do not save cost.

## Measure your models

In a source checkout, run the app evaluation without routing (`pi-frontier`) and with routing
(`pi-hybrid`), then print the report to compare pass rate and cost per build.

```sh
export OPENROUTER_API_KEY=sk-or-...
export THETARELAY_LITE_API_KEY=$OPENROUTER_API_KEY
export THETARELAY_FRONTIER_MODEL=<frontier model> THETARELAY_LITE_MODEL=<lite model>
uv run python scripts/app_eval/run.py run --runs 3 --specs bookmarks --arms pi-frontier,pi-hybrid
uv run python scripts/app_eval/run.py report
```

This runs 6 agent sessions with your models: 3 per arm.

## Release run

The full comparison runs every arm on every spec, 10 times each: 12 arms, 3 specs, 360 agent
sessions. Each routed arm (`judge`, `triage`, `hybrid`) is followed by a random arm that sends
the same share of steps to the lite model, so the report can tell routing apart from chance.
List each routed arm before its random arm. A random arm skips itself until its routed arm has
a turn log in the same results folder, and the run loop goes through the arms in the order you
list them.

Before you start:

- The `claude` arm needs Claude Code with its normal configuration: the Headroom proxy in
  `ANTHROPIC_BASE_URL` (from your settings) and the RTK hook.
- The `pi-*` arms need the `pi` and `rtk` CLIs on your `PATH`, and `OPENROUTER_API_KEY` set.

Use a fresh results folder (`APP_EVAL_RESULTS`). Otherwise the run reuses builds and lite
shares from earlier runs, which may have used other model settings.

```sh
export OPENROUTER_API_KEY=sk-or-...
export THETARELAY_LITE_API_KEY=$OPENROUTER_API_KEY
APP_EVAL_RESULTS=scripts/app_eval/results-release uv run python scripts/app_eval/run.py run --runs 10 --arms claude,pi-frontier,pi-lite,pi-judge,pi-random-judge,pi-triage,pi-random-triage,pi-hybrid,pi-random-hybrid,pi-hybrid-no-stop,pi-hybrid-no-triage,pi-hybrid-no-size-limit
APP_EVAL_RESULTS=scripts/app_eval/results-release uv run python scripts/app_eval/run.py report
```

`--specs` defaults to all specs (`bookmarks`, `todo-cli`, `inventory`). The run resumes where it
stopped if you start it again.

Rough cost: about 20 to 30 USD per spec of the size of `bookmarks`, from our earlier
`bookmarks` builds (mean API price per session: Claude Code 0.48 USD, Pi with Opus alone 0.20
USD, Pi with routing 0.13 to 0.16 USD). `todo-cli` is smaller and `inventory` is larger, so
expect 60 to 100 USD in total and about 10 hours. Run one spec first (`--specs todo-cli
--runs 1`) to check your own numbers.
