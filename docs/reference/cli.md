# Command-line interface

```text
thetarelay [proxy | judge | report] [OPTIONS]
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

## `thetarelay report`

Reads a turn log and prints the actual cost and an estimate of the cost with the frontier model
only. See [How to check your savings](../how-to/check-your-savings.md).

| Argument or option | Value | Default |
|---|---|---|
| `PATH` | Turn log file (`THETARELAY_TURN_LOG`) | Required |
| `--frontier-model` | Model to price the estimate against | Most common frontier model in the log |
| `--offline` | Flag. Does not fetch prices from OpenRouter. | Fetches prices |
| `--price-input` | Input price, USD per million tokens | None |
| `--price-output` | Output price, USD per million tokens | None |
| `--price-cache-read` | Cache read price, USD per million tokens | Input price |
| `--price-cache-write` | Cache write price, USD per million tokens | Input price |
| `--json` | Flag. Prints one JSON object in place of the text report. | Text report |

The prices apply only if you give both `--price-input` and `--price-output`. They replace the
OpenRouter lookup.

The text report has one row for each tier (requests, tokens and cost), the actual cost, the
frontier-only estimate, the saving, and the count of each routing reason. A value shows `n/a`
when it is not known, for example the estimate if the price lookup fails or if you use
`--offline` without prices. Lines that are not valid JSON are skipped and counted.

`--json` prints the same data: `tiers`, `actual_usd`, `estimate_usd`, `saving_pct`,
`no_cost_requests`, `reasons`, `skipped_lines`, `frontier_model`, `prices_usd_per_million`,
`prices_source` and `price_error`.

```sh
thetarelay report turns.jsonl --offline --price-input 5 --price-output 25
```

## `just` recipes

| Recipe | Runs |
|---|---|
| `just setup` | `uv sync --locked` |
| `just check` | Lint, format check, type check and tests |
| `just fmt` | Format and apply safe lint fixes |
| `just run` | `uv run thetarelay` |
