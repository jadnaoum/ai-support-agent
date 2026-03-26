"""
Tests for the action service node.
Uses real test DB for tool execution; no LLM calls.
"""
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.action_service import action_service_node
from backend.db.models import Customer, Order, Product, OrderItem


def make_state(tool: str = "track_order", params: dict = None, **overrides) -> dict:
    base = {
        "messages": [{"role": "customer", "content": "Track my order"}],
        "customer_id": "",  # overridden per test
        "customer_context": {},
        "retrieved_context": [],
        "action_results": [],
        "confidence": 0.0,
        "requires_escalation": False,
        "escalation_reason": "",
        "actions_taken": [],
        "response": "",
        "pending_service": "action",
        "pending_action": {"tool": tool, "params": params or {}},
    }
    base.update(overrides)
    return base


@pytest.fixture
async def customer(db: AsyncSession):
    c = Customer(id=str(uuid.uuid4()), name="Test User", email=f"act-{uuid.uuid4()}@example.com")
    db.add(c)
    await db.commit()
    return c


@pytest.fixture
async def placed_order(db: AsyncSession, customer):
    product = Product(
        id=str(uuid.uuid4()), name="Widget", category="electronics",
        price=50.00, return_window_days=14,
    )
    db.add(product)
    await db.flush()
    order = Order(
        id=str(uuid.uuid4()), customer_id=customer.id,
        status="placed", total_amount=50.00,
    )
    db.add(order)
    await db.flush()
    db.add(OrderItem(
        id=str(uuid.uuid4()), order_id=order.id, product_id=product.id,
        quantity=1, price_at_purchase=50.00,
    ))
    await db.commit()
    return order


# ---------------------------------------------------------------------------
# Routing and structure
# ---------------------------------------------------------------------------

async def test_clears_pending_service(db, customer, placed_order):
    state = make_state("track_order", customer_id=customer.id)
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert result["pending_service"] == ""


async def test_clears_pending_action(db, customer, placed_order):
    state = make_state("track_order", customer_id=customer.id)
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert result["pending_action"] == {}


async def test_appends_to_action_results(db, customer, placed_order):
    state = make_state("track_order", customer_id=customer.id)
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert len(result["action_results"]) == 1


async def test_appends_to_actions_taken(db, customer, placed_order):
    state = make_state("track_order", customer_id=customer.id)
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert len(result["actions_taken"]) == 1
    assert result["actions_taken"][0]["service"] == "action_service"
    assert result["actions_taken"][0]["action"] == "track_order"


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

async def test_track_order_succeeds(db, customer, placed_order):
    state = make_state("track_order", {"order_id": placed_order.id}, customer_id=customer.id)
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert result["action_results"][0]["success"] is True
    assert result["action_results"][0]["status"] == "placed"


async def test_cancel_order_succeeds(db, customer, placed_order):
    state = make_state("cancel_order", {"order_id": placed_order.id}, customer_id=customer.id)
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert result["action_results"][0]["success"] is True


async def test_unknown_tool_returns_error(db, customer):
    state = make_state("fly_to_moon", customer_id=customer.id)
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert result["action_results"][0]["success"] is False
    assert "unknown action" in result["action_results"][0]["error"].lower()


async def test_null_params_are_stripped(db, customer, placed_order):
    """Null params from LLM extraction should not be passed to tool handlers."""
    state = make_state(
        "track_order",
        {"order_id": None},  # null order_id should use most recent
        customer_id=customer.id,
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert result["action_results"][0]["success"] is True


# ---------------------------------------------------------------------------
# process_refund — risk_score injection
# ---------------------------------------------------------------------------

@pytest.fixture
async def delivered_order(db: AsyncSession, customer):
    """Delivered recently — within return window, low total to avoid high-value flag."""
    product = Product(
        id=str(uuid.uuid4()), name="Refund Widget", category="clothing",
        price=30.00, return_window_days=30, final_sale=False,
    )
    db.add(product)
    await db.flush()
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()), customer_id=customer.id,
        status="delivered", total_amount=30.00,
        delivered_at=now - timedelta(days=3),
    )
    db.add(order)
    await db.flush()
    db.add(OrderItem(
        id=str(uuid.uuid4()), order_id=order.id, product_id=product.id,
        quantity=1, price_at_purchase=30.00,
    ))
    await db.commit()
    return order


async def test_risk_score_injected_from_customer_context(db, customer, delivered_order):
    """action_service must inject risk_score from customer_context, not from LLM params."""
    state = make_state(
        "process_refund",
        {"order_id": delivered_order.id},  # LLM provides order_id only — no risk_score
        customer_id=customer.id,
        customer_context={"risk_score": 0.9},  # high risk — should trigger pending_review
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    tool_result = result["action_results"][0]
    assert tool_result["success"] is True
    assert tool_result["status"] == "pending_review"


async def test_risk_score_cannot_be_overridden_by_llm(db, customer, delivered_order):
    """Even if the LLM somehow provides risk_score=0.0 in params, action_service overwrites it."""
    state = make_state(
        "process_refund",
        {"order_id": delivered_order.id, "risk_score": 0.0},  # LLM-provided — must be ignored
        customer_id=customer.id,
        customer_context={"risk_score": 0.9},  # actual risk from customer context
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    tool_result = result["action_results"][0]
    assert tool_result["success"] is True
    # The real risk_score (0.9) must have been used — not the LLM-supplied 0.0
    assert tool_result["status"] == "pending_review"


async def test_low_risk_score_from_context_approves_refund(db, customer, delivered_order):
    """Low risk_score in customer_context → approved (not pending_review)."""
    state = make_state(
        "process_refund",
        {"order_id": delivered_order.id},
        customer_id=customer.id,
        customer_context={"risk_score": 0.1},
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    tool_result = result["action_results"][0]
    assert tool_result["success"] is True
    assert tool_result["status"] == "approved"


async def test_missing_customer_context_defaults_to_zero_risk(db, customer, delivered_order):
    """No customer_context in state → risk_score defaults to 0.0 → approved."""
    state = make_state(
        "process_refund",
        {"order_id": delivered_order.id},
        customer_id=customer.id,
        customer_context={},  # no risk_score field
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    tool_result = result["action_results"][0]
    assert tool_result["success"] is True
    assert tool_result["status"] == "approved"
