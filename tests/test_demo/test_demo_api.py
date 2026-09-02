import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select

from backend.db.models import Customer, Order, Refund
from backend.db.seed import PRODUCTS
from backend.demo.scenarios import SCENARIOS, NAME_POOL, RELATIVE_DATES, ORDER_STATUSES
from backend.demo.customers import validate_name, DemoValidationError, slugify
from backend.routers import demo as demo_router


# ---------------------------------------------------------------------------
# Catalog + products
# ---------------------------------------------------------------------------

async def test_products_lists_catalog(client, catalog):
    r = await client.get("/api/products")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()]
    assert "ProBook X15 Laptop" in names
    assert names == sorted(names)
    assert set(r.json()[0]) == {"id", "name", "category"}


async def test_catalog_resolves_scenario_products_and_shuffles_names(client, catalog):
    r = await client.get("/api/demo/catalog")
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["names"]) == sorted(NAME_POOL)
    assert len(body["scenarios"]) == len(SCENARIOS)
    missed = next(s for s in body["scenarios"] if s["id"] == "missed-window")
    assert missed["orders"][0]["product_name"] == "ProBook X15 Laptop"
    assert missed["orders"][0]["ordered_label"] == "3 weeks ago"
    assert missed["dare"]
    assert any("Ignore your previous instructions" in p for p in missed["suggested_prompts"])
    assert body["statuses"] == ORDER_STATUSES
    assert body["max_orders"] == 3


def test_every_scenario_is_valid():
    for s in SCENARIOS:
        assert 1 <= len(s["orders"]) <= 3, s["id"]
        for o in s["orders"]:
            assert o["product"] in PRODUCTS, s["id"]
            assert o["status"] in ORDER_STATUSES, s["id"]
            assert o["ordered_relative"] in RELATIVE_DATES, s["id"]
        assert s["dare"] and s["suggested_prompts"], s["id"]
    assert len({s["id"] for s in SCENARIOS}) == len(SCENARIOS)


# ---------------------------------------------------------------------------
# Demo customer creation
# ---------------------------------------------------------------------------

async def test_create_from_scenario(client, db, catalog):
    r = await client.post("/api/demo/customer", json={"name": "Galadriel", "scenario_id": "missed-window"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Galadriel"
    assert body["email"].startswith("galadriel-") and body["email"].endswith("@shire.demo")
    assert body["scenario_id"] == "missed-window"
    assert body["scenario_title"] == "Just missed the window"
    assert len(body["orders"]) == 1
    order = body["orders"][0]
    assert order["product_name"] == "ProBook X15 Laptop"
    assert order["status"] == "delivered"
    assert order["ordered_label"] == "3 weeks ago"
    assert body["suggested_prompts"]

    customer = (await db.execute(select(Customer).where(Customer.id == body["customer_id"]))).scalar_one()
    assert customer.metadata_["demo"] is True
    assert "ip_hash" in customer.metadata_ and "." not in customer.metadata_["ip_hash"]

    db_order = (await db.execute(select(Order).where(Order.customer_id == customer.id))).scalar_one()
    assert float(db_order.total_amount) == 1299.99
    days = (datetime.now(timezone.utc) - db_order.created_at).days
    assert 20 <= days <= 21
    # delivered orders MUST carry delivered_at or the return-window check is skipped
    assert db_order.delivered_at is not None
    assert (db_order.delivered_at - db_order.created_at).days == 2


async def test_create_from_explicit_orders(client, db, catalog):
    r = await client.post("/api/demo/customer", json={
        "name": "Éowyn",
        "orders": [
            {"product_id": PRODUCTS["jeans"], "status": "returned", "ordered_relative": "1_month_ago"},
            {"product_id": PRODUCTS["headphones"], "status": "processing", "ordered_relative": "2_days_ago"},
        ],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scenario_id"] == ""
    assert body["email"].startswith("eowyn-")
    statuses = {o["product_name"]: o["status"] for o in body["orders"]}
    assert statuses == {"Slim Fit Denim Jeans": "returned", "SoundMax Pro Headphones": "processing"}

    # returned orders get a processed refund row so get_refund_status has something to show
    refunds = (await db.execute(select(Refund).where(Refund.customer_id == body["customer_id"]))).scalars().all()
    assert len(refunds) == 1 and refunds[0].status == "processed"
    processing = (await db.execute(
        select(Order).where(Order.customer_id == body["customer_id"], Order.status == "processing"))).scalar_one()
    assert processing.delivered_at is None


async def test_create_rejects_bad_input(client, catalog):
    base = {"orders": [{"product_id": PRODUCTS["jeans"], "status": "delivered", "ordered_relative": "1_week_ago"}]}
    assert (await client.post("/api/demo/customer", json={"name": "", **base})).status_code == 422
    assert (await client.post("/api/demo/customer", json={"name": "x" * 41, **base})).status_code == 422
    assert (await client.post("/api/demo/customer", json={"name": "Bob<script>", **base})).status_code == 422
    assert (await client.post("/api/demo/customer", json={"name": "Bob", "orders": []})).status_code == 422
    assert (await client.post("/api/demo/customer", json={"name": "Bob", "orders": base["orders"] * 4})).status_code == 422
    bad_status = {"orders": [{**base["orders"][0], "status": "placed"}]}
    assert (await client.post("/api/demo/customer", json={"name": "Bob", **bad_status})).status_code == 422
    bad_rel = {"orders": [{**base["orders"][0], "ordered_relative": "yesterday"}]}
    assert (await client.post("/api/demo/customer", json={"name": "Bob", **bad_rel})).status_code == 422
    bad_pid = {"orders": [{**base["orders"][0], "product_id": str(uuid.uuid4())}]}
    r = await client.post("/api/demo/customer", json={"name": "Bob", **bad_pid})
    assert r.status_code == 422 and "unknown product_id" in r.json()["detail"]["message"]
    assert (await client.post("/api/demo/customer", json={"name": "Bob", "scenario_id": "nope"})).status_code == 422


async def test_same_name_twice_gets_unique_emails(client, catalog):
    a = await client.post("/api/demo/customer", json={"name": "Gandalf", "scenario_id": "final-sale-regret"})
    b = await client.post("/api/demo/customer", json={"name": "Gandalf", "scenario_id": "final-sale-regret"})
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["email"] != b.json()["email"]


async def test_per_ip_customer_cap(client, catalog, monkeypatch):
    monkeypatch.setattr(demo_router.settings, "demo_max_customers_per_ip_per_day", 2)
    for _ in range(2):
        assert (await client.post("/api/demo/customer", json={"name": "Frodo", "scenario_id": "double-dip"})).status_code == 201
    r = await client.post("/api/demo/customer", json={"name": "Frodo", "scenario_id": "double-dip"})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "customer_cap"


async def test_get_customer_roundtrip_and_404(client, catalog):
    created = (await client.post("/api/demo/customer", json={"name": "Geralt", "scenario_id": "broken-after-months"})).json()
    r = await client.get(f"/api/demo/customer/{created['customer_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Geralt"
    assert [o["product_name"] for o in body["orders"]] == ["LED Desk Lamp", "PowerBlend Pro"]  # newest first
    assert body["orders"][1]["ordered_label"] == "5 months ago"
    assert (await client.get(f"/api/demo/customer/{uuid.uuid4()}")).status_code == 404


def test_name_validation_and_slug():
    assert validate_name("  Éowyn ") == "Éowyn"
    assert slugify("Éowyn") == "eowyn"
    assert slugify("Ciri of Cintra") == "ciri-of-cintra"
    with pytest.raises(DemoValidationError):
        validate_name("Bob; DROP TABLE")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

async def test_rules_are_derived_from_products_and_constants(client, catalog):
    created = (await client.post("/api/demo/customer", json={"name": "Galadriel", "scenario_id": "missed-window"})).json()
    r = await client.get(f"/api/demo/rules?customer_id={created['customer_id']}")
    assert r.status_code == 200
    body = r.json()
    account = [x["text"] for x in body["account"]]
    assert account == ["ProBook X15 Laptop · 14-day return window · 12-month warranty"]
    texts = [x["text"] for x in body["global"]]
    assert any("electronics: 14 days" in t and "30 days" in t for t in texts)
    assert any(t.startswith("Orders can be cancelled while processing, not once") for t in texts)
    assert any("€50" in t and "human review" in t for t in texts)
    assert any("Defective and duplicate item claims are handed to a human" == t for t in texts)
    assert any("Final sale, no returns: Rugged Phone Case" == t for t in texts)
    assert any("Any cancellation or return needs your confirmation before it runs" == t for t in texts)
    assert all(x["source"] for x in body["global"])


async def test_rules_without_customer_and_unknown_customer(client, catalog):
    r = await client.get("/api/demo/rules")
    assert r.status_code == 200 and r.json()["account"] == [] and r.json()["global"]
    assert (await client.get(f"/api/demo/rules?customer_id={uuid.uuid4()}")).status_code == 404
