# Command-line interface

```text
thetarelay [proxy | judge] [OPTIONS]
```

With no command, `thetarelay` runs `proxy`. All other settings are environment variables. See
[Configuration](configuration.md).

## `thetarelay proxy`

Starts the routing proxy.

| Option | Value | Default |
|---|---|---|
| `--host` | Bind address | `THETARELAY_HOST` or `127.0.0.1` |
| `--port` | Bind port | `THETARELAY_PORT` or `8788` |
| `--debug` | Flag. Prints one line to standard error for each request. | `THETARELAY_DEBUG` |
| `--no-headroom-middleware` | Flag. Does not load Headroom compression. | Loads it if installed |

## `thetarelay judge`

Sends one step to the judge and prints the answer as JSON. Use it to examine the judge and the
threshold.

| Argument or option | Value |
|---|---|
| `last_message` | Last message of the conversation |
| `--goal` | What the user asked for |

```sh
thetarelay judge --goal "Fix the date test" "[tool bash] FAILED tests/test_dates.py::test_dst"
```

## `just` recipes

| Recipe | Runs |
|---|---|
| `just setup` | `uv sync --locked` |
| `just check` | Lint, format check, type check and tests |
| `just fmt` | Format and apply safe lint fixes |
| `just run` | `uv run thetarelay` |
