"""Shared database helpers for the reference build."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from pydantic import AfterValidator

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, name TEXT NOT NULL,
    price_cents INTEGER NOT NULL, stock INTEGER NOT NULL CHECK (stock >= 0));
CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, customer TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS order_items (order_id INTEGER NOT NULL, sku TEXT NOT NULL,
    quantity INTEGER NOT NULL, price_cents INTEGER NOT NULL);
"""


def init(path: str) -> None:
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.close()


@contextmanager
def tx(path: str) -> Iterator[sqlite3.Connection]:
    """One connection and one `BEGIN IMMEDIATE` transaction per request."""
    db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def norm_sku(v: str) -> str:
    return v.strip().upper()


def _text(v: str) -> str:
    if not v.strip():
        raise ValueError("must not be blank")
    return v.strip()


Text = Annotated[str, AfterValidator(_text)]
