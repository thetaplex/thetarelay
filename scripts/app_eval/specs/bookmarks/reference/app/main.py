"""Reference implementation of SPEC.md, used only to validate the hidden tests."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, field_validator


def _tags(tags: list[str] | None) -> list[str]:
    return sorted({t.strip().lower() for t in tags or [] if t.strip()})


class New(BaseModel):
    url: str
    title: str | None = None
    tags: list[str] | None = None

    @field_validator("url")
    @classmethod
    def valid(cls, v: str) -> str:
        p = urlparse(v)
        if p.scheme not in ("http", "https") or not p.netloc:
            raise ValueError("invalid url")
        return v


class Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    tags: list[str] | None = None


def create_app(db_path: str) -> FastAPI:
    db = sqlite3.connect(db_path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE IF NOT EXISTS b (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE,"
        " title TEXT, tags TEXT, created_at TEXT)"
    )
    app = FastAPI()

    def out(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"],
            "url": r["url"],
            "title": r["title"],
            "tags": [t for t in r["tags"].split(",") if t],
            "created_at": r["created_at"],
        }

    def get(i: int) -> sqlite3.Row:
        r = db.execute("SELECT * FROM b WHERE id=?", (i,)).fetchone()
        if r is None:
            raise HTTPException(404, "not found")
        return r

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/bookmarks", status_code=201)
    def create(b: New) -> dict[str, Any]:
        title = (b.title or "").strip() or b.url
        try:
            cur = db.execute(
                "INSERT INTO b (url,title,tags,created_at) VALUES (?,?,?,?)",
                (b.url, title, ",".join(_tags(b.tags)), datetime.now(UTC).isoformat()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "duplicate url") from None
        db.commit()
        return out(get(cur.lastrowid or 0))

    @app.get("/bookmarks")
    def list_(
        tag: str | None = None,
        q: str | None = None,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        rows = [out(r) for r in db.execute("SELECT * FROM b ORDER BY id DESC")]
        if tag is not None:
            t = tag.strip().lower()
            rows = [r for r in rows if t in r["tags"]]
        if q:
            rows = [r for r in rows if q.lower() in (r["title"] + r["url"]).lower()]
        return rows[offset : offset + limit]

    @app.get("/bookmarks/{i}")
    def one(i: int) -> dict[str, Any]:
        return out(get(i))

    @app.patch("/bookmarks/{i}")
    def patch(i: int, p: Patch) -> dict[str, Any]:
        r = get(i)
        if p.title is not None:
            db.execute("UPDATE b SET title=? WHERE id=?", (p.title.strip() or r["url"], i))
        if p.tags is not None:
            db.execute("UPDATE b SET tags=? WHERE id=?", (",".join(_tags(p.tags)), i))
        db.commit()
        return out(get(i))

    @app.delete("/bookmarks/{i}", status_code=204)
    def delete(i: int) -> Response:
        get(i)
        db.execute("DELETE FROM b WHERE id=?", (i,))
        db.commit()
        return Response(status_code=204)

    @app.get("/tags")
    def tags() -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for r in db.execute("SELECT tags FROM b"):
            for t in filter(None, r["tags"].split(",")):
                counts[t] = counts.get(t, 0) + 1
        return [{"name": n, "count": c} for n, c in sorted(counts.items())]

    return app


app = create_app(os.environ.get("BOOKMARKS_DB", "bookmarks.db"))
