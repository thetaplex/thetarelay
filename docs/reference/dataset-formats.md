# Reference: dataset formats

Both formats are JSON Lines: one JSON object on each line. thetarelay ignores empty lines. The
encoding is UTF-8.

## Scenario file (`bench --dataset`, `eval --dataset`)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `request_body` | object | Yes | — | Anthropic Messages or OpenAI Chat Completions request body. |
| `id` | string | No | `custom_<line>` | Scenario identifier. |
| `title` | string | No | `Custom Scenario <line>` | Human-readable name. |
| `category` | string | No | `code_edit` | `code_edit`, `tool_call`, `openai_prediction`, `rejection_guard` or `bypass`. |
| `provider` | string | No | `anthropic` | `anthropic` or `openai`. |
| `expected_text` | string | No | `""` | Response that the frontier model gives without a draft. |
| `expected_tool_calls` | array | No | `[]` | Expected tool calls. Each item: `{"name": string, "input": object}`. |
| `simulated_draft_text` | string | No | `expected_text` | Draft text for `bench`. |
| `simulated_draft_tool_calls` | array | No | `expected_tool_calls` | Draft tool calls for `bench`. |
| `draft_is_accurate` | boolean | No | `true` | For `bench`: the simulated frontier accepts the draft if `true`. |

The loader does not read the latency and token fields of the built-in scenarios. Custom scenarios
use the defaults: 8000 cached prompt tokens, 400 uncached prompt tokens, 240 output tokens,
280 ms draft time and 420 ms frontier time to first token.

Example line:

```json
{"id": "sum_list", "provider": "anthropic", "request_body": {"model": "claude-sonnet-5-5", "max_tokens": 256, "messages": [{"role": "user", "content": "Write sum_list(xs) that returns the sum of xs."}]}, "expected_text": "```python\ndef sum_list(xs):\n    return sum(xs)\n```"}
```

## HumanEval file (`eval --benchmark humaneval --dataset`)

thetarelay reads the format of [openai/human-eval](https://github.com/openai/human-eval). It reads
`.jsonl` and gzip-compressed `.jsonl.gz` files.

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Problem identifier, for example `HumanEval/0`. |
| `entry_point` | string | Yes | Name of the function under test. |
| `prompt` | string | Yes | Function signature and docstring. |
| `test` | string | Yes | Python source that defines `check(candidate)`. |
| `canonical_solution` | string | No | Reference solution. thetarelay does not use it. |
