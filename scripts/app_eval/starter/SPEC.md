# Bookmarks API

Build a small REST API for saving and organising bookmarks.

## Stack

- Python 3.13, FastAPI, SQLite (the standard-library `sqlite3` module). No ORM.
- Use `uv` for everything: `uv add <package>` to add a dependency, `uv run pytest` to run tests.
  The dependencies in `pyproject.toml` are already enough.

## Layout

- `app/main.py` must define `create_app(db_path: str) -> FastAPI`. Each call returns an app that
  stores its data in the SQLite file at `db_path`, creating the file and tables if needed.
- Also define a module-level `app = create_app(os.environ.get("BOOKMARKS_DB", "bookmarks.db"))`
  so that `uv run uvicorn app.main:app` works.
- Put your own tests in `tests/`.

## Data

A bookmark has:

| Field | Type | Rules |
|---|---|---|
| `id` | integer | Assigned by the server, starts at 1, increases. |
| `url` | string | Required. Must start with `http://` or `https://` and have a host. Unique. |
| `title` | string | Optional. Defaults to the URL. Leading and trailing spaces are removed. |
| `tags` | list of strings | Optional, default `[]`. Each tag is trimmed and lower-cased; empty tags are dropped; duplicates are removed; the list is sorted alphabetically. |
| `created_at` | string | ISO 8601 UTC timestamp, set by the server when the bookmark is created. |

## Endpoints

All bodies are JSON.

### `POST /bookmarks`

Body: `{"url": ..., "title": ..., "tags": [...]}` (`title` and `tags` optional).

- `201` with the created bookmark.
- `422` if `url` is missing or not a valid `http(s)` URL.
- `409` if a bookmark with the same `url` already exists. Body: `{"detail": "duplicate url"}`.

### `GET /bookmarks`

Returns `200` with a list of bookmarks, newest first (highest `id` first).

Query parameters, all optional and combinable:

- `tag`: only bookmarks that have this tag (compared after the same normalisation as above).
- `q`: only bookmarks whose `title` or `url` contains this text, case-insensitive.
- `limit`: maximum number of results, 1–100, default 20. Outside that range: `422`.
- `offset`: number of results to skip, 0 or more, default 0. Negative: `422`.

### `GET /bookmarks/{id}`

`200` with the bookmark, or `404` with `{"detail": "not found"}`.

### `PATCH /bookmarks/{id}`

Body: any of `title`, `tags`. Only the given fields change; the same rules apply (an empty or
blank `title` resets it to the URL). `200` with the updated bookmark, `404` if it does not exist.
`url` cannot be changed: if the body contains `url`, return `422`.

### `DELETE /bookmarks/{id}`

`204` with no body, or `404` if it does not exist.

### `GET /tags`

`200` with `[{"name": ..., "count": ...}]`: every tag in use and the number of bookmarks that
have it, sorted by `name`. Tags with no bookmarks are not listed.

### `GET /health`

`200` with `{"status": "ok"}`.
