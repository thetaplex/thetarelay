# Reference: command-line interface

```text
thetacode [proxy | bench | eval] [OPTIONS]
```

If you do not give a command, thetacode starts `proxy` with default options. To give options, you
must give the command name.

Each option overrides the related [environment variable](configuration.md). The order of
precedence is: option, environment variable, default.

## `thetacode proxy`

Starts the standalone HTTP proxy. The proxy writes a banner to standard error and logs at the
`INFO` level.

| Option | Value | Default | Environment variable |
|---|---|---|---|
| `--host` | Host name or IP address | `127.0.0.1` | `THETACODE_HOST` |
| `--port` | TCP port | `8788` | `THETACODE_PORT` |
| `--local-url` | Base URL of the local OpenAI-compatible API | `http://127.0.0.1:11434/v1` | `THETACODE_LOCAL_BASE_URL` |
| `--local-model` | Local draft model name | `qwen2.5-coder:7b` | `THETACODE_LOCAL_MODEL` |
| `--strategy` | `auto`, `accept_swap`, `prediction`, `context_only` | `auto` | `THETACODE_STRATEGY` |
| `--anthropic-upstream` | Base URL of the Anthropic API | `https://api.anthropic.com` | `THETACODE_ANTHROPIC_UPSTREAM` |
| `--openai-upstream` | Base URL of the OpenAI API | `https://api.openai.com` | `THETACODE_OPENAI_UPSTREAM` |
| `--no-headroom-middleware` | Flag | Off | `THETACODE_HEADROOM_MIDDLEWARE=false` |

thetacode removes a trailing `/` from all URL options.

## `thetacode bench`

Runs the deterministic A/B benchmark. The benchmark uses `httpx.MockTransport` and does not use
the network. It replaces the local and upstream URLs with test URLs.

| Option | Value | Default |
|---|---|---|
| `--json` | Flag. Writes the full report as JSON. | Table |
| `--dataset` | Path to a scenario JSONL file | Built-in scenarios |
| `--strategy` | `auto`, `accept_swap`, `prediction`, `context_only` | `THETACODE_STRATEGY` or `auto` |

## `thetacode eval`

Evaluates a live local model. The output is always JSON.

| Option | Value | Default |
|---|---|---|
| `--local-url` | Base URL of the local OpenAI-compatible API | `THETACODE_LOCAL_BASE_URL` |
| `--local-model` | Local draft model name | `THETACODE_LOCAL_MODEL` |
| `--target-model` | Target model name. Turns on the two-phase comparison. | None |
| `--target-url` | Base URL of the target OpenAI-compatible API | Value of `--local-url` |
| `--benchmark` | `scenarios` or `humaneval` | `scenarios` |
| `--dataset` | Path to a scenario JSONL file, or a HumanEval `.jsonl` or `.jsonl.gz` file | Built-in set |
| `--limit` | Maximum number of problems. Applies to `humaneval` only. | All |
| `--json` | Flag. Has no effect. | — |

The `scenarios` benchmark does not run scenarios with the category `bypass`.

## `just` recipes

| Recipe | Command |
|---|---|
| `just setup` | `uv sync --locked` |
| `just test` | `uv run pytest` |
| `just lint` | `ruff check`, `ruff format --check`, `mypy src tests` |
| `just fmt` | `ruff format`, `ruff check --fix` |
| `just check` | `lint` and `test`. CI runs this recipe. |
| `just bench` | `uv run thetacode bench` |
| `just eval` | `uv run thetacode eval` |
| `just run` | `uv run thetacode` |

All recipes except `setup`, `lint`, `fmt` and `check` pass extra arguments to the command.
