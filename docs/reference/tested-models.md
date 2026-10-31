# Reference: tested models

thetarelay works with any model that uses the Anthropic Messages API or the OpenAI Chat Completions
API. This page lists the models that were tested, and the result. A model that is not on this page
is not tested.

Last update: 2026-10-31. Frontier models were called through OpenRouter. Local models ran in
Ollama on an Apple silicon Mac.

## Frontier models (verifier)

The frontier model decides if the draft is correct. If it accepts a draft with a bug, the agent
gets the bug. Use only a confirmed model.

### Confirmed

These models were tested with the `accept_swap` strategy of thetarelay 0.1.0.

| Model | Tests | Result |
|---|---|---|
| `anthropic/claude-opus-4.5` | HumanEval (10 problems), coding-agent scenarios, agent traces, drafts with bugs | 9 of 10 HumanEval drafts accepted, 10 of 10 correct. 0 of 2 drafts with bugs accepted. |
| `openai/gpt-5.4` | HumanEval (10 problems), agent traces, drafts with bugs | 9 of 10 HumanEval drafts accepted, 10 of 10 correct. 0 of 2 drafts with bugs accepted. |
| `qwen3.8:27b-mlx` (local) | Coding-agent scenarios, drafts with bugs | 4 of 4 correct drafts accepted. 0 of 3 drafts with bugs accepted. |

Raw data: [`benchmarks/results/live_benchmark_results_2026_10_30.json`](../../benchmarks/results/live_benchmark_results_2026_10_30.json).

### Works, with a low acceptance rate

| Model | Result |
|---|---|
| `openai/gpt-4.1` | 2 of 5 HumanEval drafts accepted. thetarelay saves less with this model. |

### Not recommended

These models were tested with an experimental verification prompt, not with the prompt of
thetarelay 0.1.0. The test used 10 HumanEval problems, with 15 drafts that had one bug and
10 drafts that were wrong from the first line.

| Model | Problem |
|---|---|
| `deepseek/deepseek-v4-pro-0813` | Accepted 4 of 15 drafts with a bug. |
| `qwen/qwen3-coder-next` | Accepted 1 completely wrong draft. Often identified the wrong line. |
| `anthropic/claude-sonnet-5.5` | Did not use the reply format in 17 of 35 replies. Did not accept a draft with a bug. |
| `x-ai/grok-4.7` | Correct decisions, but the provider bills its reasoning as output tokens. Verification cost more output tokens than the full answer. |

### Promising, not confirmed

These models made correct decisions in the same experimental test (35 of 35 final answers correct,
0 drafts with a bug accepted). They are not tested with the prompt of thetarelay 0.1.0.

- `google/gemini-3.8-flash`

`anthropic/claude-opus-4.5` and `openai/gpt-5.4` also had 35 of 35 correct in this test.

## Local models (drafter)

A weak drafter does not make the answer wrong, because the frontier model checks each draft. It
makes thetarelay save less.

| Model | Result |
|---|---|
| `gemma4:e4b-mlx` | Used for all results above (Apple silicon only). 9 of 10 HumanEval drafts passed the tests. Approximately 1.5 s for each HumanEval draft. |
| `gemma4:e4b` | The default of `THETARELAY_LOCAL_MODEL`. The same model as `gemma4:e4b-mlx` for Linux, Windows and macOS. On an Apple silicon Mac with `qwen3.8:27b-mlx` as verifier: 9 of 10 HumanEval drafts passed the tests and were accepted, approximately 1.2 s for each draft (0.8 s to 4.3 s). With adaptive drafting in multi-turn sessions, warm prompt cache drafts average 1.6 s, versus ~4 s on cold prefill. |
| `lfm3` | Not tested. Requires Ollama 0.40.12 or later. |

## Test a model that is not on this page

Before you use a frontier model that is not confirmed, run:

```sh
OPENROUTER_API_KEY=... just eval --benchmark humaneval \
  --local-model gemma4:e4b \
  --target-model <model> --target-url https://openrouter.ai/api/v1
```

In the output, `buggy_draft_guardrail_tests` must show `"verifier_rejected_bug": true` for each
test. If the model accepts a draft with a bug, do not use it.
