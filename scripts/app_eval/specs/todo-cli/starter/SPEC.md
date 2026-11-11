# To-do CLI

Build a small command-line to-do list that keeps its tasks in a JSON file.

## Stack

- Python 3.13, standard library only (`argparse`, `json`). No other runtime dependencies.
- Use `uv` for everything: `uv run pytest` to run tests, `uv run python -m app.cli ...` to run
  the tool. The dependencies in `pyproject.toml` are already enough.

## Layout

- `app/cli.py` must define `main(argv: list[str]) -> int`. It runs one command, writes normal
  output to stdout and error messages to stderr, and returns the exit code. When a command succeeds, nothing is written to stderr. It must not call
  `sys.exit` or let `SystemExit` escape (including from `argparse`).
- `uv run python -m app.cli <command> ...` must run `main(sys.argv[1:])` and exit with its code.
- Put your own tests in `tests/`.

## Data file

Tasks are stored in the JSON file named by the `TODO_FILE` environment variable, or `todo.json`
in the current directory if it is not set. `main` reads `TODO_FILE` (and resolves `todo.json`
against the current directory) on every call, not once at import. The file format is up to you.

- A missing file means an empty list; it is created by the first command that changes data.
- Commands that only read (`list`) never create the file.
- If the file exists but is not valid JSON, every command prints `error: cannot read <path>`
  to stderr and returns `1`, leaving the file unchanged.

## Tasks

A task has:

| Field | Rules |
|---|---|
| id | Integer, assigned on `add`, starts at 1, increases by 1. Ids are never reused, even after `remove` or `clear-done`. |
| text | Leading and trailing spaces are removed. Must not be empty after that. |
| priority | `high`, `normal` or `low`. Default `normal`. |
| done | `false` when added. |

## Commands

### `add <text> [--priority high|normal|low]`

Adds a task and prints `added <id>`. Exit `0`.

### `list [--done | --all]`

Prints one line per task:

```
<id> [ ] (<priority>) <text>
<id> [x] (<priority>) <text>
```

`[ ]` is a pending task, `[x]` a done one. For example `3 [ ] (high) Buy milk`.

- No option: pending tasks only. `--done`: done tasks only. `--all`: both, pending tasks first.
- Within each group, order by priority (`high`, then `normal`, then `low`), then by id ascending.
- If nothing matches, print `no tasks`.

Exit `0`.

### `done <id> [<id> ...]`

Marks the tasks done and prints `done <id>` for each, in the order given. Marking a done task
again is allowed and prints the same line.

### `remove <id>`

Deletes the task and prints `removed <id>`.

### `clear-done`

Deletes every done task and prints `cleared <n>`, where `n` is how many were deleted (it may
be `0`).

## Errors

| Case | stderr | Exit |
|---|---|---|
| No command, unknown command, unknown option or missing argument | any message | `2` |
| `add` text is empty after trimming | `error: empty text` | `2` |
| `--priority` is not `high`, `normal` or `low` | any message | `2` |
| An id is not a positive integer | `error: invalid id <value>` | `2` |
| An id does not exist (`done`, `remove`) | `error: no task <id>` | `1` |
| Data file is not valid JSON | `error: cannot read <path>` | `1` |

When a command fails, nothing is written to stdout and the data file is not changed. In
particular, `done 1 99` where task 99 does not exist marks nothing done.
