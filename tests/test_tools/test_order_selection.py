"""
No-order_id selection: with one order the tool uses it; with two or more it
returns order_id_required + candidate_orders instead of guessing the newest.
Same rule in the real tools (DB) and the mock layer.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.mock_tools import mock_tool_call
from backend.db.models import Customer, Order, OrderItem, Product
from backend.tools.order_tools import (
    _no_order_id_selection_data,
    check_missing_package,
    check_return_eligibility,
    track_order,
)


def test_selection_rule():
    assert _no_order_id_selection_data([], 0)[1]["error"] == "No orders found for this account."
    assert _no_order_id_selection_data([], 1) == (0, None)
    index, outcome = _no_order_id_selection_data([{"order_id": "a"}, {"order_id": "b"}], 2)
    assert index is None
    assert outcome == {"success": False, "reason": "order_id_required",
                       "candidate_orders": [{"order_id": "a"}, {"order_id": "b"}]}


# ---------------------------------------------------------------------------
# Real tools
# ---------------------------------------------------------------------------

async def _customer_with(db: AsyncSession, *orders):
    """orders: (product_name, status, days_ago) tuples."""
    c = Customer(id=str(uuid.uuid4()), name="Geralt", email=f"g-{uuid.uuid4()}@test.com")
    db.add(c)
    await db.flush()
    ids = []
    for name, status, days_ago in orders:
        p = Product(id=str(uuid.uuid4()), name=name, category="home_goods", price=50.0, warranty_months=12)
        placed = datetime.now(timezone.utc) - timedelta(days=days_ago)
        o = Order(id=str(uuid.uuid4()), customer_id=c.id, status=status, total_amount=50.0,
                  created_at=placed, delivered_at=placed + timedelta(days=3) if status == "delivered" else None)
        db.add_all([p, o])
        await db.flush()
        db.add(OrderItem(id=str(uuid.uuid4()), order_id=o.id, product_id=p.id, quantity=1, price_at_purchase=50.0))
        ids.append(o.id)
    await db.commit()
    return c, ids


@pytest.mark.parametrize("tool", [track_order, check_missing_package])
async def test_two_orders_without_id_returns_candidates(db, tool):
    c, (lamp_id, blender_id) = await _customer_with(db, ("Desk lamp", "shipped", 22), ("Blender", "delivered", 170))
    result = await tool(db, customer_id=c.id)
    assert result["reason"] == "order_id_required"
    assert [o["order_id"] for o in result["candidate_orders"]] == [lamp_id, blender_id]
    assert result["candidate_orders"][1]["items"] == [{"product": "Blender", "quantity": 1}]


async def test_single_order_without_id_is_used(db):
    c, (only_id,) = await _customer_with(db, ("Desk lamp", "shipped", 5))
    result = await track_order(db, customer_id=c.id)
    assert result["reason"] == "tracking_info"
    assert result["order_id"] == only_id


async def test_defect_claim_without_id_and_two_orders_asks(db):
    c, _ = await _customer_with(db, ("Desk lamp", "shipped", 22), ("Blender", "delivered", 170))
    result = await check_return_eligibility(db, customer_id=c.id, reason="defective")
    assert result["reason"] == "order_id_required"
    assert len(result["candidate_orders"]) == 2


async def test_defect_claim_without_id_and_one_order_checks_it(db):
    c, (blender_id,) = await _customer_with(db, ("Blender", "delivered", 170))
    result = await check_return_eligibility(db, customer_id=c.id, reason="broken")
    assert result["order_id"] == blender_id
    assert result["requires_escalation"] is True


async def test_non_defect_check_without_id_keeps_listing_eligible_orders(db):
    c, _ = await _customer_with(db, ("Desk lamp", "delivered", 5), ("Blender", "delivered", 8))
    result = await check_return_eligibility(db, customer_id=c.id, reason="changed_mind")
    assert "eligible_orders" in result
    assert "candidate_orders" not in result


# ---------------------------------------------------------------------------
# Mock layer — same rule
# ---------------------------------------------------------------------------

MOCK_TWO = {"orders": [
    {"id": "lamp-1", "status": "shipped", "total": 59.99, "placed_at": "2026-11-02",
     "items": [{"name": "Desk lamp", "category": "home_goods"}]},
    {"id": "blender-1", "status": "delivered", "total": 89.99, "placed_at": "2026-06-07",
     "delivered_date": "2026-06-12", "items": [{"name": "Blender", "category": "home_goods", "warranty_months": 12}]},
]}


@pytest.mark.parametrize("tool,params", [
    ("track_order", {"order_id": None}),
    ("check_missing_package", {"order_id": None}),
    ("check_return_eligibility", {"order_id": None, "reason": "defective"}),
])
def test_mock_two_orders_without_id_returns_candidates(tool, params):
    result = mock_tool_call(tool, params, MOCK_TWO, "cust", [])
    assert result["reason"] == "order_id_required"
    assert [o["order_id"] for o in result["candidate_orders"]] == ["lamp-1", "blender-1"]


def test_mock_single_order_without_id_is_used():
    mock = {"orders": [MOCK_TWO["orders"][1]]}
    assert mock_tool_call("track_order", {"order_id": None}, mock, "cust", [])["order_id"] == "blender-1"
    result = mock_tool_call("check_return_eligibility", {"order_id": None, "reason": "defective"}, mock, "cust", [])
    assert result["order_id"] == "blender-1"
