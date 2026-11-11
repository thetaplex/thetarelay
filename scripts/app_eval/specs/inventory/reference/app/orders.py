"""Reference order service for SPEC.md, used only to validate the hidden tests."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from app.db import Text, init, norm_sku, tx
from fastapi import FastAPI, HTTPException, Query
from pydantic import AfterValidator, BaseModel, Field


class Item(BaseModel):
    sku: Annotated[str, AfterValidator(norm_sku)]
    quantity: int = Field(ge=1)


class New(BaseModel):
    customer: Text
    items: list[Item] = Field(min_length=1)


def create_app(db_path: str) -> FastAPI:
    init(db_path)
    app = FastAPI()

    def get(db: sqlite3.Connection, i: int) -> dict[str, Any]:
        r = db.execute("SELECT * FROM orders WHERE id=?", (i,)).fetchone()
        if r is None:
            raise HTTPException(404, "not found")
        items = [
            dict(x)
            for x in db.execute(
                "SELECT sku, quantity, price_cents FROM order_items WHERE order_id=? ORDER BY sku",
                (i,),
            )
        ]
        return {
            **dict(r),
            "items": items,
            "total_cents": sum(x["quantity"] * x["price_cents"] for x in items),
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "orders"}

    @app.post("/orders", status_code=201)
    def create(o: New) -> dict[str, Any]:
        want: dict[str, int] = {}
        for it in o.items:
            want[it.sku] = want.get(it.sku, 0) + it.quantity
        with tx(db_path) as db:
            have = {
                s: db.execute("SELECT * FROM products WHERE sku=?", (s,)).fetchone() for s in want
            }
            for s in sorted(want):
                if have[s] is None:
                    raise HTTPException(422, f"unknown sku: {s}")
            for s in sorted(want):
                if have[s]["stock"] < want[s]:
                    raise HTTPException(409, f"insufficient stock: {s}")
            cur = db.execute(
                "INSERT INTO orders (customer, status, created_at) VALUES (?, 'placed', ?)",
                (o.customer, datetime.now(UTC).isoformat()),
            )
            oid = cur.lastrowid or 0
            for s, n in want.items():
                db.execute("UPDATE products SET stock=stock-? WHERE sku=?", (n, s))
                db.execute(
                    "INSERT INTO order_items VALUES (?,?,?,?)", (oid, s, n, have[s]["price_cents"])
                )
            return get(db, oid)

    @app.get("/orders")
    def list_(
        status: Literal["placed", "shipped", "cancelled"] | None = None,
        customer: str | None = None,
    ) -> list[dict[str, Any]]:
        with tx(db_path) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM orders ORDER BY id DESC")]
            out = [get(db, i) for i in ids]
        if status:
            out = [o for o in out if o["status"] == status]
        if customer is not None:
            out = [o for o in out if o["customer"] == customer.strip()]
        return out

    @app.get("/orders/{i}")
    def one(i: int) -> dict[str, Any]:
        with tx(db_path) as db:
            return get(db, i)

    def move(i: int, to: str) -> dict[str, Any]:
        with tx(db_path) as db:
            o = get(db, i)
            if o["status"] != "placed":
                raise HTTPException(409, f"order is {o['status']}")
            db.execute("UPDATE orders SET status=? WHERE id=?", (to, i))
            if to == "cancelled":
                for x in o["items"]:
                    db.execute(
                        "UPDATE products SET stock=stock+? WHERE sku=?", (x["quantity"], x["sku"])
                    )
            return get(db, i)

    @app.post("/orders/{i}/ship")
    def ship(i: int) -> dict[str, Any]:
        return move(i, "shipped")

    @app.post("/orders/{i}/cancel")
    def cancel(i: int) -> dict[str, Any]:
        return move(i, "cancelled")

    @app.get("/reports/top-products")
    def top(limit: int = Query(10, ge=1, le=100)) -> list[dict[str, Any]]:
        sql = (
            "SELECT i.sku, p.name, SUM(i.quantity) AS quantity,"
            " SUM(i.quantity * i.price_cents) AS revenue_cents"
            " FROM order_items i JOIN orders o ON o.id = i.order_id"
            " JOIN products p ON p.sku = i.sku WHERE o.status != 'cancelled'"
            " GROUP BY i.sku ORDER BY quantity DESC, i.sku LIMIT ?"
        )
        with tx(db_path) as db:
            return [dict(r) for r in db.execute(sql, (limit,))]

    return app


app = create_app(os.environ.get("INVENTORY_DB", "inventory.db"))
