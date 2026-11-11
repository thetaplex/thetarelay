"""Reference catalog service for SPEC.md, used only to validate the hidden tests."""

from __future__ import annotations

import os
import re
from typing import Annotated, Any, Literal

from app.db import Text, init, norm_sku, tx
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _sku(v: str) -> str:
    v = norm_sku(v)
    if not re.fullmatch(r"[A-Z0-9-]{3,20}", v):
        raise ValueError("invalid sku")
    return v


class New(BaseModel):
    sku: Annotated[str, AfterValidator(_sku)]
    name: Text
    price_cents: int = Field(ge=1)
    stock: int = Field(0, ge=0)


class Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Text | None = None
    price_cents: int | None = Field(None, ge=1)


class Restock(BaseModel):
    quantity: int = Field(ge=1)


ORDER = {
    "sku": "sku",
    "name": "lower(name), sku",
    "price": "price_cents, sku",
    "-price": "price_cents DESC, sku",
}


def create_app(db_path: str) -> FastAPI:
    init(db_path)
    app = FastAPI()

    def get(db: Any, sku: str) -> dict[str, Any]:
        r = db.execute("SELECT * FROM products WHERE sku=?", (norm_sku(sku),)).fetchone()
        if r is None:
            raise HTTPException(404, "not found")
        return dict(r)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "catalog"}

    @app.post("/products", status_code=201)
    def create(p: New) -> dict[str, Any]:
        with tx(db_path) as db:
            if db.execute("SELECT 1 FROM products WHERE sku=?", (p.sku,)).fetchone():
                raise HTTPException(409, "duplicate sku")
            db.execute("INSERT INTO products VALUES (?,?,?,?)", tuple(p.model_dump().values()))
            return get(db, p.sku)

    @app.get("/products")
    def list_(
        q: str | None = None,
        in_stock: bool | None = None,
        sort: Literal["sku", "name", "price", "-price"] = "sku",
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        where, args = ["1"], []
        if q:
            where.append("instr(lower(name), lower(?)) > 0")
            args.append(q)
        if in_stock is not None:
            where.append("stock > 0" if in_stock else "stock = 0")
        sql = f"SELECT * FROM products WHERE {' AND '.join(where)} ORDER BY {ORDER[sort]}"  # noqa: S608
        with tx(db_path) as db:
            rows = [dict(r) for r in db.execute(sql + " LIMIT ? OFFSET ?", (*args, limit, offset))]
        return rows

    @app.get("/products/{sku}")
    def one(sku: str) -> dict[str, Any]:
        with tx(db_path) as db:
            return get(db, sku)

    @app.patch("/products/{sku}")
    def patch(sku: str, p: Patch) -> dict[str, Any]:
        with tx(db_path) as db:
            cur = get(db, sku)
            for k, v in p.model_dump(exclude_unset=True).items():
                if v is not None:
                    db.execute(f"UPDATE products SET {k}=? WHERE sku=?", (v, cur["sku"]))  # noqa: S608
            return get(db, sku)

    @app.post("/products/{sku}/restock")
    def restock(sku: str, r: Restock) -> dict[str, Any]:
        with tx(db_path) as db:
            cur = get(db, sku)
            db.execute("UPDATE products SET stock=stock+? WHERE sku=?", (r.quantity, cur["sku"]))
            return get(db, sku)

    @app.delete("/products/{sku}", status_code=204)
    def delete(sku: str) -> Response:
        with tx(db_path) as db:
            cur = get(db, sku)
            if db.execute("SELECT 1 FROM order_items WHERE sku=?", (cur["sku"],)).fetchone():
                raise HTTPException(409, "product has orders")
            db.execute("DELETE FROM products WHERE sku=?", (cur["sku"],))
        return Response(status_code=204)

    return app


app = create_app(os.environ.get("INVENTORY_DB", "inventory.db"))
