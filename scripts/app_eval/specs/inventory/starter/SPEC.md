# Inventory Services

Build two small REST services, a catalog and an order service, that share one SQLite database
file. The catalog owns products and stock; the order service places orders that take stock from
the catalog.

## Stack

- Python 3.13, FastAPI, SQLite (the standard-library `sqlite3` module). No ORM.
- Use `uv` for everything: `uv add <package>` to add a dependency, `uv run pytest` to run tests.
  The dependencies in `pyproject.toml` are already enough.

## Layout

- `app/catalog.py` and `app/orders.py` must each define `create_app(db_path: str) -> FastAPI`.
  Each call returns an app that uses the SQLite file at `db_path`.
- Each module also defines a module-level
  `app = create_app(os.environ.get("INVENTORY_DB", "inventory.db"))`, so that
  `uv run uvicorn app.catalog:app` and `uv run uvicorn app.orders:app --port 8001` work.
- Either service may start first: each `create_app` creates the file and any missing tables.
- Put your own tests in `tests/`.

## Sharing the database

The two services run as separate processes, and several instances of each may run at once, all
on the same file. So:

- A write by any instance is visible to every other instance on its next request. Do not cache
  rows in memory.
- Requests may arrive concurrently, from many threads and from many instances. Stock must never
  go below zero and must never be lost: placing, cancelling and restocking must each be atomic
  (use a transaction, for example `BEGIN IMMEDIATE`, and wait for locks rather than failing).

## Data

A product has:

| Field | Type | Rules |
|---|---|---|
| `sku` | string | Required. Leading and trailing spaces are removed and it is upper-cased; after that it must be 3–20 characters of `A`–`Z`, `0`–`9` and `-`. Unique. |
| `name` | string | Required. Trimmed; must not be empty after that. |
| `price_cents` | integer | Required, 1 or more. |
| `stock` | integer | Optional, default `0`, 0 or more. |

An order has:

| Field | Type | Rules |
|---|---|---|
| `id` | integer | Assigned by the server, starts at 1, increases. |
| `customer` | string | Required. Trimmed; must not be empty after that. |
| `status` | string | `placed`, `shipped` or `cancelled`. `placed` when created. |
| `items` | list | `[{"sku": ..., "quantity": ..., "price_cents": ...}]`, sorted by `sku`. `price_cents` is the product's price when the order was placed; later price changes do not affect it. |
| `total_cents` | integer | Sum of `quantity * price_cents` over the items. |
| `created_at` | string | ISO 8601 UTC timestamp, set by the server. |

Wherever a SKU appears in a path or a request body, it is normalised as above before use, so
`/products/ab-1` finds the product `AB-1`.

Error bodies are `{"detail": "<message>"}` with the exact messages given below. `422` responses
for invalid bodies or query parameters may use any body.

## Catalog service (`app/catalog.py`)

### `GET /health`

`200` with `{"status": "ok", "service": "catalog"}`.

### `POST /products`

Body: `{"sku": ..., "name": ..., "price_cents": ..., "stock": ...}` (`stock` optional).

- `201` with the created product.
- `422` if any field is missing or breaks the rules above.
- `409` with `{"detail": "duplicate sku"}` if the SKU already exists.

### `GET /products`

`200` with a list of products. Query parameters, all optional and combinable:

- `q`: only products whose `name` contains this text, case-insensitive.
- `in_stock`: `true` for only products with `stock > 0`, `false` for only products with
  `stock == 0`.
- `sort`: `sku` (default, A to Z), `name` (A to Z, case-insensitive, ties by `sku`), `price`
  (lowest first, ties by `sku`) or `-price` (highest first, ties by `sku`). Anything else: `422`.
- `limit`: 1–100, default 50. `offset`: 0 or more, default 0. Outside those ranges: `422`.
  Paging applies after filtering and sorting.

### `GET /products/{sku}`

`200` with the product, or `404` with `{"detail": "not found"}`.

### `PATCH /products/{sku}`

Body: any of `name`, `price_cents`, with the same rules. Only the given fields change. `200`
with the updated product, `404` if it does not exist. Any other field in the body (including
`sku` and `stock`): `422`, and nothing changes.

### `POST /products/{sku}/restock`

Body: `{"quantity": n}` with `n` 1 or more, else `422`. Adds `n` to `stock`. `200` with the
updated product, `404` if it does not exist.

### `DELETE /products/{sku}`

`204` with no body. `404` if it does not exist. `409` with `{"detail": "product has orders"}`
if any order (in any status) contains it; the product is kept.

## Order service (`app/orders.py`)

### `GET /health`

`200` with `{"status": "ok", "service": "orders"}`.

### `POST /orders`

Body: `{"customer": ..., "items": [{"sku": ..., "quantity": ...}, ...]}`.

- `items` must be a non-empty list and each `quantity` 1 or more; otherwise `422`.
- Items with the same SKU (after normalisation) are merged by adding their quantities.
- `422` with `{"detail": "unknown sku: <SKU>"}` if a SKU does not exist.
- `409` with `{"detail": "insufficient stock: <SKU>"}` if a product has less stock than the
  quantity ordered.
- If several SKUs are unknown or short, report the first one in SKU order; unknown SKUs are
  checked before stock.
- On success, the stock of every product in the order goes down by its quantity, and the
  response is `201` with the order. On any error, no stock changes and no order is created.

### `GET /orders`

`200` with a list of orders, newest first (highest `id` first). Optional query parameters:

- `status`: `placed`, `shipped` or `cancelled`; anything else `422`.
- `customer`: only orders whose `customer` equals this value after trimming (case-sensitive).

### `GET /orders/{id}`

`200` with the order, or `404` with `{"detail": "not found"}`.

### `POST /orders/{id}/ship`

Changes a `placed` order to `shipped`. `200` with the order.

### `POST /orders/{id}/cancel`

Changes a `placed` order to `cancelled` and puts its quantities back into stock. `200` with
the order.

For both: `404` if the order does not exist; `409` with `{"detail": "order is <status>"}` (for
example `order is shipped`) if it is not `placed`, and nothing changes.

### `GET /reports/top-products`

`200` with `[{"sku": ..., "name": ..., "quantity": ..., "revenue_cents": ...}]`: for each
product in at least one `placed` or `shipped` order, the total quantity ordered and the total
of `quantity * price_cents` over those orders. Cancelled orders do not count. Sorted by
`quantity` (highest first), ties by `sku`. Optional `limit`: 1–100, default 10, else `422`.
