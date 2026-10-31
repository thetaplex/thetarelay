# Reference: command-line interface

```text
thetarelay [proxy | bench | eval] [OPTIONS]
```

If you do not give a command, thetarelay starts `proxy` with default options. To give options, you
must give the command name.

Each option overrides the related [environment variable](configuration.md). The order of
precedence is: option, environment variable, default.

## `thetarelay proxy`

Starts the standalone HTTP proxy. The proxy writes a banner to standard error and logs at the
`INFO` level.

| Option | Value | Default | Environment variable |
|---|---|---|---|
| `--host` | Host name or IP address | `127.0.0.1` | `THETARELAY_HOST` |
| `--port` | TCP port | `8788` | `THETARELAY_PORT` |
| `--local-url` | Base URL of the local OpenAI-compatible API | `http://127.0.0.1:11434/v1` | `THETARELAY_LOCAL_BASE_URL` |
| `--local-model` | Local draft model name | `gemma4:e4b` | `THETARELAY_LOCAL_MODEL` |
| `--strategy` | `auto`, `accept_swap`, `prediction`, `context_only` | `auto` | `THETARELAY_STRATEGY` |
| `--usecase` | `coding` | `coding` | `THETARELAY_USECASE` |
| `--anthropic-upstream` | Base URL of the Anthropic API | `https://api.anthropic.com` | `THETARELAY_ANTHROPIC_UPSTREAM` |
| `--openai-upstream` | Base URL of the OpenAI API | `https://api.openai.com` | `THETARELAY_OPENAI_UPSTREAM` |
| `--no-headroom-middleware` | Flag | Off | `THETARELAY_HEADROOM_MIDDLEWARE=false` |

thetarelay removes a trailing `/` from all URL options.

Additional proxy behavior, including adaptive drafting (`THETARELAY_LOCAL_ADAPTIVE_CONTEXT`),
draft timeouts (`THETARELAY_LOCAL_TIMEOUT`), and character ceilings
(`THETARELAY_LOCAL_MAX_CONTEXT_CHARS`), is configured through [environment variables](configuration.md).

## `thetarelay bench`

Runs the deterministic A/B benchmark. The benchmark uses `httpx.MockTransport` and does not use
the network. It replaces the local and upstream URLs with test URLs. `bench` evaluates isolated
scenarios and does not maintain multi-turn adaptive context state.

| Option | Value | Default |
|---|---|---|
| `--json` | Flag. Writes the full report as JSON. | Table |
| `--dataset` | Path to a scenario JSONL file | Built-in scenarios |
| `--strategy` | `auto`, `accept_swap`, `prediction`, `context_only` | `THETARELAY_STRATEGY` or `auto` |
| `--usecase` | `coding` | `THETARELAY_USECASE` or `coding` |

## `thetarelay eval`

Evaluates a live local model. The output is always JSON. `eval` tests individual benchmark
problems with a fixed context window without multi-turn adaptive context tracking.

| Option | Value | Default |
|---|---|---|
| `--local-url` | Base URL of the local OpenAI-compatible API | `THETARELAY_LOCAL_BASE_URL` |
| `--local-model` | Local draft model name | `THETARELAY_LOCAL_MODEL` |
| `--target-model` | Target model name. Turns on the two-phase comparison. | None |
| `--target-url` | Base URL of the target OpenAI-compatible API | Value of `--local-url` |
| `--usecase` | `coding` | `THETARELAY_USECASE` or `coding` |
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
| `just bench` | `uv run thetarelay bench` |
| `just eval` | `uv run thetarelay eval` |
| `just run` | `uv run thetarelay` |

All recipes except `setup`, `lint`, `fmt` and `check` pass extra arguments to the command.
