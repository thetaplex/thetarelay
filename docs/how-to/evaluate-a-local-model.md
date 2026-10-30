# How to evaluate a local draft model

Use `thetacode eval` to measure a local model on coding-agent tasks before you use it in the proxy.
The output is JSON.

> **WARNING:** The `humaneval` benchmark runs code that a model wrote. It runs the code in a child
> process with a time limit only. The code can read and change files and can use the network.
> Run it only in a container or a virtual machine.

## Evaluate the draft model on the built-in scenarios

1. Start the local model server.
2. Run the evaluation:

   ```sh
   just eval --local-url http://127.0.0.1:11434/v1 --local-model qwen2.5-coder:7b
   ```

3. Read these fields:

   | Field | Meaning |
   |---|---|
   | `valid_draft_rate_pct` | Percentage of drafts that are syntactically valid and use the expected tool. |
   | `avg_local_latency_ms` | Average draft time. |
   | `results[].exact_match` | `true` if the draft is equal to the expected response. |

## Compare the draft model with a target model

The target model must have an OpenAI-compatible Chat Completions endpoint.

1. Add `--target-model`. If the target uses a different server, add `--target-url` also:

   ```sh
   just eval --local-model qwen2.5-coder:7b \
     --target-model qwen2.5-coder:32b \
     --target-url http://127.0.0.1:11434/v1
   ```

2. Read `two_phase_target_verification` in the output. It shows the baseline time, the
   speculative time, the speedup and the number of accepted drafts.

thetacode runs all draft turns first and all target turns after. This prevents model swaps on a
server that has memory for one model only.

## Measure functional accuracy with HumanEval

1. Run the 10 built-in problems:

   ```sh
   just eval --benchmark humaneval --local-model qwen2.5-coder:7b
   ```

2. To run the full HumanEval set, download `HumanEval.jsonl.gz` from
   [openai/human-eval](https://github.com/openai/human-eval). Then add `--dataset`:

   ```sh
   just eval --benchmark humaneval --dataset HumanEval.jsonl.gz --limit 50 \
     --local-model qwen2.5-coder:7b --target-model qwen2.5-coder:32b
   ```

3. Read `local_pass_at_1_pct`. With a target model, read `target_comparison` also.

For a hosted target, set `THETACODE_TARGET_API_KEY` (or `OPENROUTER_API_KEY` or
`OPENAI_API_KEY`) to the key of the target provider. For `--benchmark scenarios`, set
`THETACODE_LOCAL_API_KEY` in its place. See [Known limitations](../reference/known-limitations.md).

## Use your own scenarios

1. Write a JSONL file. See [Dataset formats](../reference/dataset-formats.md).
2. Add `--dataset`:

   ```sh
   just eval --dataset my-scenarios.jsonl --local-model qwen2.5-coder:7b
   ```

You can use the same file with `just bench --dataset my-scenarios.jsonl`. The benchmark uses the
`simulated_draft_*` fields and does not call a model.
