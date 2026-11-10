"""Hidden acceptance tests for SPEC.md. The agents never see this file."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "db.sqlite")))


def add(client: TestClient, url: str, **kw: object) -> dict:
    r = client.post("/bookmarks", json={"url": url, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# --- create ---------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_create_returns_full_bookmark(client: TestClient) -> None:
    b = add(client, "https://example.com", title="Example", tags=["Web"])
    assert b["id"] == 1
    assert b["url"] == "https://example.com"
    assert b["title"] == "Example"
    assert b["tags"] == ["web"]
    assert datetime.fromisoformat(b["created_at"].replace("Z", "+00:00"))


def test_ids_increase(client: TestClient) -> None:
    assert add(client, "https://a.com")["id"] == 1
    assert add(client, "https://b.com")["id"] == 2


def test_title_defaults_to_url_and_is_trimmed(client: TestClient) -> None:
    assert add(client, "https://a.com")["title"] == "https://a.com"
    assert add(client, "https://b.com", title="  Hello  ")["title"] == "Hello"


def test_tags_are_normalised(client: TestClient) -> None:
    b = add(client, "https://a.com", tags=[" Python", "web", "PYTHON", "", "  ", "api"])
    assert b["tags"] == ["api", "python", "web"]


def test_tags_default_empty(client: TestClient) -> None:
    assert add(client, "https://a.com")["tags"] == []


@pytest.mark.parametrize("url", ["example.com", "ftp://example.com", "https://", "", "http//x.com"])
def test_invalid_url_rejected(client: TestClient, url: str) -> None:
    assert client.post("/bookmarks", json={"url": url}).status_code == 422


def test_missing_url_rejected(client: TestClient) -> None:
    assert client.post("/bookmarks", json={"title": "x"}).status_code == 422


def test_duplicate_url_conflict(client: TestClient) -> None:
    add(client, "https://a.com")
    r = client.post("/bookmarks", json={"url": "https://a.com"})
    assert r.status_code == 409
    assert r.json() == {"detail": "duplicate url"}


# --- read -----------------------------------------------------------------------------


def test_get_by_id_and_404(client: TestClient) -> None:
    b = add(client, "https://a.com")
    assert client.get(f"/bookmarks/{b['id']}").json() == b
    r = client.get("/bookmarks/999")
    assert r.status_code == 404
    assert r.json() == {"detail": "not found"}


def test_list_newest_first(client: TestClient) -> None:
    for u in ("https://a.com", "https://b.com", "https://c.com"):
        add(client, u)
    assert [b["url"] for b in client.get("/bookmarks").json()] == [
        "https://c.com",
        "https://b.com",
        "https://a.com",
    ]


def test_filter_by_tag_is_normalised(client: TestClient) -> None:
    add(client, "https://a.com", tags=["python"])
    add(client, "https://b.com", tags=["rust"])
    r = client.get("/bookmarks", params={"tag": " Python "})
    assert [b["url"] for b in r.json()] == ["https://a.com"]


def test_search_title_and_url_case_insensitive(client: TestClient) -> None:
    add(client, "https://docs.python.org", title="Docs")
    add(client, "https://rust-lang.org", title="The PYTHON alternative")
    add(client, "https://go.dev", title="Go")
    urls = {b["url"] for b in client.get("/bookmarks", params={"q": "python"}).json()}
    assert urls == {"https://docs.python.org", "https://rust-lang.org"}


def test_filters_combine(client: TestClient) -> None:
    add(client, "https://a.com", title="python tips", tags=["blog"])
    add(client, "https://b.com", title="python docs", tags=["docs"])
    r = client.get("/bookmarks", params={"q": "python", "tag": "docs"})
    assert [b["url"] for b in r.json()] == ["https://b.com"]


def test_default_limit_is_20(client: TestClient) -> None:
    for i in range(25):
        add(client, f"https://site{i}.com")
    assert len(client.get("/bookmarks").json()) == 20


def test_limit_and_offset(client: TestClient) -> None:
    for i in range(5):
        add(client, f"https://site{i}.com")
    r = client.get("/bookmarks", params={"limit": 2, "offset": 1})
    assert [b["url"] for b in r.json()] == ["https://site3.com", "https://site2.com"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_bad_paging_rejected(client: TestClient, params: dict) -> None:
    assert client.get("/bookmarks", params=params).status_code == 422


# --- update ---------------------------------------------------------------------------


def test_patch_title_only(client: TestClient) -> None:
    b = add(client, "https://a.com", title="Old", tags=["x"])
    r = client.patch(f"/bookmarks/{b['id']}", json={"title": " New "})
    assert r.status_code == 200
    assert r.json()["title"] == "New"
    assert r.json()["tags"] == ["x"]


def test_patch_tags_normalised(client: TestClient) -> None:
    b = add(client, "https://a.com", tags=["x"])
    r = client.patch(f"/bookmarks/{b['id']}", json={"tags": ["B", "a", "b"]})
    assert r.json()["tags"] == ["a", "b"]
    assert client.get(f"/bookmarks/{b['id']}").json()["tags"] == ["a", "b"]


def test_patch_blank_title_resets_to_url(client: TestClient) -> None:
    b = add(client, "https://a.com", title="T")
    assert client.patch(f"/bookmarks/{b['id']}", json={"title": "  "}).json()["title"] == (
        "https://a.com"
    )


def test_patch_url_rejected(client: TestClient) -> None:
    b = add(client, "https://a.com")
    assert client.patch(f"/bookmarks/{b['id']}", json={"url": "https://b.com"}).status_code == 422
    assert client.get(f"/bookmarks/{b['id']}").json()["url"] == "https://a.com"


def test_patch_missing_404(client: TestClient) -> None:
    assert client.patch("/bookmarks/999", json={"title": "x"}).status_code == 404


# --- delete ---------------------------------------------------------------------------


def test_delete(client: TestClient) -> None:
    b = add(client, "https://a.com")
    r = client.delete(f"/bookmarks/{b['id']}")
    assert r.status_code == 204
    assert r.content == b""
    assert client.get(f"/bookmarks/{b['id']}").status_code == 404
    assert client.delete(f"/bookmarks/{b['id']}").status_code == 404


def test_url_can_be_reused_after_delete(client: TestClient) -> None:
    b = add(client, "https://a.com")
    client.delete(f"/bookmarks/{b['id']}")
    add(client, "https://a.com")


# --- tags -----------------------------------------------------------------------------


def test_tags_counts_sorted(client: TestClient) -> None:
    add(client, "https://a.com", tags=["web", "python"])
    add(client, "https://b.com", tags=["python"])
    assert client.get("/tags").json() == [
        {"name": "python", "count": 2},
        {"name": "web", "count": 1},
    ]


def test_tags_drop_unused(client: TestClient) -> None:
    b = add(client, "https://a.com", tags=["gone"])
    add(client, "https://b.com", tags=["kept"])
    client.delete(f"/bookmarks/{b['id']}")
    assert client.get("/tags").json() == [{"name": "kept", "count": 1}]


# --- persistence ----------------------------------------------------------------------


def test_data_persists_across_app_instances(tmp_path: Path) -> None:
    db = str(tmp_path / "db.sqlite")
    TestClient(create_app(db)).post("/bookmarks", json={"url": "https://a.com", "tags": ["t"]})
    again = TestClient(create_app(db)).get("/bookmarks").json()
    assert [(b["url"], b["tags"]) for b in again] == [("https://a.com", ["t"])]


def test_separate_databases_are_isolated(tmp_path: Path) -> None:
    one = TestClient(create_app(str(tmp_path / "one.sqlite")))
    two = TestClient(create_app(str(tmp_path / "two.sqlite")))
    one.post("/bookmarks", json={"url": "https://a.com"})
    assert two.get("/bookmarks").json() == []
