"""Hidden acceptance tests for SPEC.md. The agents never see this file."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from app import catalog, orders
from fastapi.testclient import TestClient


@pytest.fixture
def db(tmp_path: Path) -> str:
    return str(tmp_path / "inventory.sqlite")


@pytest.fixture
def cat(db: str) -> TestClient:
    return TestClient(catalog.create_app(db))


@pytest.fixture
def ords(db: str, cat: TestClient) -> TestClient:
    return TestClient(orders.create_app(db))


def product(cat: TestClient, sku: str, price: int = 100, stock: int = 10, **kw: Any) -> dict:
    body = {"sku": sku, "name": kw.pop("name", f"Product {sku}"), "price_cents": price}
    r = cat.post("/products", json={**body, "stock": stock, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def order(ords: TestClient, *items: tuple[str, int], customer: str = "ann") -> dict:
    body = {"customer": customer, "items": [{"sku": s, "quantity": q} for s, q in items]}
    r = ords.post("/orders", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def stock(cat: TestClient, sku: str) -> int:
    return cat.get(f"/products/{sku}").json()["stock"]


def skus(r: Any) -> list[str]:
    return [p["sku"] for p in r.json()]


# --- catalog: products ------------------------------------------------------------------


def test_health(cat: TestClient, ords: TestClient) -> None:
    assert cat.get("/health").json() == {"status": "ok", "service": "catalog"}
    assert ords.get("/health").json() == {"status": "ok", "service": "orders"}


def test_create_product_normalises(cat: TestClient) -> None:
    p = product(cat, "  ab-12 ", name="  Widget  ", price=250, stock=3)
    assert p == {"sku": "AB-12", "name": "Widget", "price_cents": 250, "stock": 3}


def test_stock_defaults_to_zero(cat: TestClient) -> None:
    r = cat.post("/products", json={"sku": "AAA", "name": "A", "price_cents": 1})
    assert r.status_code == 201
    assert r.json()["stock"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {"sku": "AB", "name": "x", "price_cents": 1},
        {"sku": "AB_1", "name": "x", "price_cents": 1},
        {"sku": "ABC", "name": "  ", "price_cents": 1},
        {"sku": "ABC", "name": "x", "price_cents": 0},
        {"name": "x", "price_cents": 1},
    ],
)
def test_invalid_product_rejected(cat: TestClient, body: dict) -> None:
    assert cat.post("/products", json=body).status_code == 422


def test_duplicate_sku_after_normalising(cat: TestClient) -> None:
    product(cat, "ABC")
    r = cat.post("/products", json={"sku": " abc ", "name": "x", "price_cents": 1})
    assert r.status_code == 409
    assert r.json() == {"detail": "duplicate sku"}


def test_get_product_by_any_case_and_404(cat: TestClient) -> None:
    p = product(cat, "AB-1")
    assert cat.get("/products/ab-1").json() == p
    r = cat.get("/products/NOPE")
    assert r.status_code == 404
    assert r.json() == {"detail": "not found"}


def test_list_sorted_by_sku_by_default(cat: TestClient) -> None:
    for s in ("CCC", "AAA", "BBB"):
        product(cat, s)
    assert skus(cat.get("/products")) == ["AAA", "BBB", "CCC"]


def test_list_sort_orders(cat: TestClient) -> None:
    product(cat, "AAA", price=300, name="banana")
    product(cat, "BBB", price=100, name="Apple")
    product(cat, "CCC", price=300, name="cherry")
    product(cat, "DDD", price=200, name="apple")
    assert skus(cat.get("/products", params={"sort": "name"})) == ["BBB", "DDD", "AAA", "CCC"]
    assert skus(cat.get("/products", params={"sort": "price"})) == ["BBB", "DDD", "AAA", "CCC"]
    assert skus(cat.get("/products", params={"sort": "-price"})) == ["AAA", "CCC", "DDD", "BBB"]


def test_list_bad_sort(cat: TestClient) -> None:
    assert cat.get("/products", params={"sort": "stock"}).status_code == 422


def test_list_filters(cat: TestClient) -> None:
    product(cat, "AAA", name="Red Widget", stock=0)
    product(cat, "BBB", name="Blue widget", stock=5)
    product(cat, "CCC", name="Gadget", stock=1)
    assert skus(cat.get("/products", params={"q": "WIDGET"})) == ["AAA", "BBB"]
    assert skus(cat.get("/products", params={"in_stock": "true"})) == ["BBB", "CCC"]
    assert skus(cat.get("/products", params={"in_stock": "false"})) == ["AAA"]
    assert skus(cat.get("/products", params={"q": "widget", "in_stock": "true"})) == ["BBB"]


def test_list_paging_after_sort(cat: TestClient) -> None:
    for i, price in enumerate([5, 1, 4, 2, 3]):
        product(cat, f"P-{i}", price=price)
    r = cat.get("/products", params={"sort": "price", "limit": 2, "offset": 1})
    assert [p["price_cents"] for p in r.json()] == [2, 3]


def test_default_limit_is_50(cat: TestClient) -> None:
    for i in range(55):
        product(cat, f"P-{i:02d}")
    assert len(cat.get("/products").json()) == 50


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_bad_paging(cat: TestClient, params: dict) -> None:
    assert cat.get("/products", params=params).status_code == 422


def test_patch_product(cat: TestClient) -> None:
    product(cat, "ABC", price=100, name="Old", stock=4)
    r = cat.patch("/products/abc", json={"name": " New "})
    assert r.status_code == 200
    assert r.json() == {"sku": "ABC", "name": "New", "price_cents": 100, "stock": 4}
    assert cat.patch("/products/ABC", json={"price_cents": 150}).json()["price_cents"] == 150
    assert cat.get("/products/ABC").json()["name"] == "New"


@pytest.mark.parametrize("body", [{"stock": 99}, {"sku": "XYZ"}])
def test_patch_rejected_changes_nothing(cat: TestClient, body: dict) -> None:
    p = product(cat, "ABC")
    assert cat.patch("/products/ABC", json=body).status_code == 422
    assert cat.get("/products/ABC").json() == p


def test_patch_missing(cat: TestClient) -> None:
    assert cat.patch("/products/NOPE", json={"name": "x"}).status_code == 404


def test_restock(cat: TestClient) -> None:
    product(cat, "ABC", stock=2)
    r = cat.post("/products/abc/restock", json={"quantity": 5})
    assert r.status_code == 200
    assert r.json()["stock"] == 7
    assert cat.post("/products/ABC/restock", json={"quantity": 0}).status_code == 422
    assert cat.post("/products/NOPE/restock", json={"quantity": 1}).status_code == 404


def test_delete_product(cat: TestClient) -> None:
    product(cat, "ABC")
    r = cat.delete("/products/abc")
    assert r.status_code == 204
    assert r.content == b""
    assert cat.get("/products/ABC").status_code == 404
    assert cat.delete("/products/ABC").status_code == 404


def test_delete_product_with_orders_conflict(cat: TestClient, ords: TestClient) -> None:
    product(cat, "ABC")
    o = order(ords, ("ABC", 1))
    ords.post(f"/orders/{o['id']}/cancel")
    r = cat.delete("/products/ABC")
    assert r.status_code == 409
    assert r.json() == {"detail": "product has orders"}
    assert cat.get("/products/ABC").status_code == 200


# --- orders -----------------------------------------------------------------------------


def test_place_order(cat: TestClient, ords: TestClient) -> None:
    product(cat, "BBB", price=250, stock=5)
    product(cat, "AAA", price=100, stock=5)
    o = order(ords, ("bbb", 2), ("AAA", 1), ("BBB", 1), customer="  ann ")
    assert o["id"] == 1
    assert o["customer"] == "ann"
    assert o["status"] == "placed"
    assert o["items"] == [
        {"sku": "AAA", "quantity": 1, "price_cents": 100},
        {"sku": "BBB", "quantity": 3, "price_cents": 250},
    ]
    assert o["total_cents"] == 850
    assert datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
    assert stock(cat, "AAA") == 4
    assert stock(cat, "BBB") == 2


def test_order_ids_increase(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA")
    assert order(ords, ("AAA", 1))["id"] == 1
    assert order(ords, ("AAA", 1))["id"] == 2


@pytest.mark.parametrize(
    "body",
    [
        {"customer": "ann", "items": []},
        {"customer": " ", "items": [{"sku": "AAA", "quantity": 1}]},
        {"customer": "ann", "items": [{"sku": "AAA", "quantity": 0}]},
    ],
)
def test_invalid_order_rejected(cat: TestClient, ords: TestClient, body: dict) -> None:
    product(cat, "AAA", stock=5)
    assert ords.post("/orders", json=body).status_code == 422
    assert stock(cat, "AAA") == 5
    assert ords.get("/orders").json() == []


def test_unknown_sku_reported_first_in_order(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA", stock=5)
    body = {
        "customer": "ann",
        "items": [
            {"sku": "ZZZ", "quantity": 1},
            {"sku": "aaa", "quantity": 99},
            {"sku": "mmm", "quantity": 1},
        ],
    }
    r = ords.post("/orders", json=body)
    assert r.status_code == 422
    assert r.json() == {"detail": "unknown sku: MMM"}
    assert stock(cat, "AAA") == 5


def test_insufficient_stock_is_atomic(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA", stock=5)
    product(cat, "BBB", stock=1)
    product(cat, "CCC", stock=1)
    body = {
        "customer": "ann",
        "items": [
            {"sku": "CCC", "quantity": 2},
            {"sku": "AAA", "quantity": 3},
            {"sku": "BBB", "quantity": 2},
        ],
    }
    r = ords.post("/orders", json=body)
    assert r.status_code == 409
    assert r.json() == {"detail": "insufficient stock: BBB"}
    assert [stock(cat, s) for s in ("AAA", "BBB", "CCC")] == [5, 1, 1]
    assert ords.get("/orders").json() == []


def test_merged_quantities_checked_against_stock(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA", stock=3)
    body = {
        "customer": "ann",
        "items": [{"sku": "AAA", "quantity": 2}, {"sku": "aaa", "quantity": 2}],
    }
    assert ords.post("/orders", json=body).status_code == 409
    assert stock(cat, "AAA") == 3


def test_order_can_take_all_stock(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA", stock=3)
    order(ords, ("AAA", 3))
    assert stock(cat, "AAA") == 0
    assert skus(cat.get("/products", params={"in_stock": "false"})) == ["AAA"]


def test_price_captured_at_order_time(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA", price=100)
    o = order(ords, ("AAA", 2))
    cat.patch("/products/AAA", json={"price_cents": 999})
    again = ords.get(f"/orders/{o['id']}").json()
    assert again["items"][0]["price_cents"] == 100
    assert again["total_cents"] == 200


def test_get_order_and_404(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA")
    o = order(ords, ("AAA", 1))
    assert ords.get(f"/orders/{o['id']}").json() == o
    r = ords.get("/orders/999")
    assert r.status_code == 404
    assert r.json() == {"detail": "not found"}


def test_list_orders_newest_first_and_filters(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA")
    a = order(ords, ("AAA", 1), customer="ann")
    b = order(ords, ("AAA", 1), customer="bob")
    c = order(ords, ("AAA", 1), customer="ann")
    ords.post(f"/orders/{c['id']}/ship")
    ids = lambda r: [o["id"] for o in r.json()]  # noqa: E731
    assert ids(ords.get("/orders")) == [c["id"], b["id"], a["id"]]
    assert ids(ords.get("/orders", params={"customer": " ann "})) == [c["id"], a["id"]]
    assert ids(ords.get("/orders", params={"customer": "ANN"})) == []
    assert ids(ords.get("/orders", params={"status": "placed"})) == [b["id"], a["id"]]
    assert ids(ords.get("/orders", params={"status": "shipped", "customer": "ann"})) == [c["id"]]
    assert ords.get("/orders", params={"status": "lost"}).status_code == 422


# --- order status -----------------------------------------------------------------------


def test_ship_keeps_stock_taken(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA", stock=5)
    o = order(ords, ("AAA", 2))
    r = ords.post(f"/orders/{o['id']}/ship")
    assert r.status_code == 200
    assert r.json()["status"] == "shipped"
    assert stock(cat, "AAA") == 3


def test_cancel_restores_stock(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA", stock=5)
    product(cat, "BBB", stock=5)
    o = order(ords, ("AAA", 2), ("BBB", 5))
    r = ords.post(f"/orders/{o['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert (stock(cat, "AAA"), stock(cat, "BBB")) == (5, 5)


@pytest.mark.parametrize(
    ("first", "then", "status"),
    [
        ("ship", "cancel", "shipped"),
        ("cancel", "ship", "cancelled"),
    ],
)
def test_bad_transition(
    cat: TestClient, ords: TestClient, first: str, then: str, status: str
) -> None:
    product(cat, "AAA", stock=5)
    o = order(ords, ("AAA", 2))
    ords.post(f"/orders/{o['id']}/{first}")
    before = stock(cat, "AAA")
    r = ords.post(f"/orders/{o['id']}/{then}")
    assert r.status_code == 409
    assert r.json() == {"detail": f"order is {status}"}
    assert ords.get(f"/orders/{o['id']}").json()["status"] == status
    assert stock(cat, "AAA") == before


def test_transition_missing_order(ords: TestClient) -> None:
    assert ords.post("/orders/999/ship").status_code == 404
    assert ords.post("/orders/999/cancel").status_code == 404


# --- reports ----------------------------------------------------------------------------


def test_top_products(cat: TestClient, ords: TestClient) -> None:
    product(cat, "AAA", price=100, stock=50, name="Apple")
    product(cat, "BBB", price=300, stock=50, name="Banana")
    product(cat, "CCC", price=50, stock=50, name="Cherry")
    product(cat, "DDD", price=10, stock=50, name="Date")
    order(ords, ("AAA", 2), ("BBB", 3))
    shipped = order(ords, ("CCC", 3))
    ords.post(f"/orders/{shipped['id']}/ship")
    cat.patch("/products/AAA", json={"price_cents": 1000})
    order(ords, ("AAA", 1))
    gone = order(ords, ("DDD", 40))
    ords.post(f"/orders/{gone['id']}/cancel")
    assert ords.get("/reports/top-products").json() == [
        {"sku": "AAA", "name": "Apple", "quantity": 3, "revenue_cents": 1200},
        {"sku": "BBB", "name": "Banana", "quantity": 3, "revenue_cents": 900},
        {"sku": "CCC", "name": "Cherry", "quantity": 3, "revenue_cents": 150},
    ]
    assert skus(ords.get("/reports/top-products", params={"limit": 1})) == ["AAA"]
    assert ords.get("/reports/top-products", params={"limit": 0}).status_code == 422


# --- sharing the database ---------------------------------------------------------------


def test_services_see_each_others_writes(db: str) -> None:
    cat_a = TestClient(catalog.create_app(db))
    cat_b = TestClient(catalog.create_app(db))
    ords = TestClient(orders.create_app(db))
    product(cat_a, "AAA", stock=5)
    assert stock(cat_b, "AAA") == 5
    order(ords, ("AAA", 2))
    assert stock(cat_a, "AAA") == 3
    cat_b.post("/products/AAA/restock", json={"quantity": 10})
    assert stock(cat_a, "AAA") == 13
    assert order(ords, ("AAA", 13))["total_cents"] == 1300


def test_orders_service_can_start_first(tmp_path: Path) -> None:
    db = str(tmp_path / "fresh.sqlite")
    ords = TestClient(orders.create_app(db))
    assert ords.get("/orders").json() == []
    cat = TestClient(catalog.create_app(db))
    product(cat, "AAA")
    assert order(ords, ("AAA", 1))["id"] == 1


def test_data_persists_across_app_instances(db: str) -> None:
    product(TestClient(catalog.create_app(db)), "AAA", stock=4)
    assert stock(TestClient(catalog.create_app(db)), "AAA") == 4


def test_concurrent_orders_never_oversell(db: str) -> None:
    product(TestClient(catalog.create_app(db)), "AAA", stock=10)
    services = [TestClient(orders.create_app(db)) for _ in range(4)]

    def buy(i: int) -> int:
        body = {"customer": f"c{i}", "items": [{"sku": "AAA", "quantity": 1}]}
        return services[i % 4].post("/orders", json=body).status_code

    with ThreadPoolExecutor(16) as pool:
        codes = list(pool.map(buy, range(30)))
    assert sorted(codes) == [201] * 10 + [409] * 20
    cat = TestClient(catalog.create_app(db))
    assert stock(cat, "AAA") == 0
    assert len(services[0].get("/orders").json()) == 10


def test_concurrent_restock_and_cancel_lose_nothing(db: str) -> None:
    cats = [TestClient(catalog.create_app(db)) for _ in range(3)]
    ords = [TestClient(orders.create_app(db)) for _ in range(3)]
    product(cats[0], "AAA", stock=20)
    placed = [order(ords[0], ("AAA", 1))["id"] for _ in range(20)]

    def work(i: int) -> int:
        if i < 20:
            return ords[i % 3].post(f"/orders/{placed[i]}/cancel").status_code
        return cats[i % 3].post("/products/AAA/restock", json={"quantity": 2}).status_code

    with ThreadPoolExecutor(16) as pool:
        codes = list(pool.map(work, range(40)))
    assert codes == [200] * 40
    assert stock(cats[0], "AAA") == 20 + 20 * 2
