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
| OpenAI (GPT-6.1 Terra, GPT-6 Astra) | No saving. These models do not hand steps to the lite model. |
| Low-cost models such as GPT-6.1 Terra | Little to save. Use the frontier model without ThetaRelay. |

Small local lite models, for example `gemma4:e4b`, often stop the agent. ThetaRelay then asks
the frontier model again, so they add time and do not save cost.

## Measure your models

In a source checkout, run the app evaluation once without routing and once with routing. Then
compare `api_cost_usd` in the two `builds.jsonl` files.

```sh
export OPENROUTER_API_KEY=sk-or-...
export THETARELAY_LITE_API_KEY=$OPENROUTER_API_KEY
export THETARELAY_FRONTIER_MODEL=<frontier model> THETARELAY_LITE_MODEL=<lite model>
APP_EVAL_RESULTS=scripts/app_eval/results-off THETARELAY_ROUTE_MODE=frontier \
  uv run python scripts/app_eval/run.py run --runs 3 --arms pi
APP_EVAL_RESULTS=scripts/app_eval/results-on \
  uv run python scripts/app_eval/run.py run --runs 3 --arms pi
```

Each command runs 3 agent sessions with your models.
