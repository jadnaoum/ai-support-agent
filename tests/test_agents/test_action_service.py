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


def _confirmed(tool_name: str, order_id: str) -> list:
    """Return an actions_taken list that satisfies the confirmation gate for one call."""
    return [{"action": tool_name, "order_id": order_id, "confirmation_required": True}]


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
        "last_clarification_source": "",
        "context_summary": "",
        "consecutive_blocks": 0,
        "service_call_count": 0,
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


async def test_actions_taken_records_order_id(db, customer, placed_order):
    """action_service must store the resolved order_id in the actions_taken entry."""
    state = make_state("track_order", {"order_id": placed_order.id}, customer_id=customer.id)
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert result["actions_taken"][0]["order_id"] == placed_order.id


async def test_actions_taken_records_confirmation_required(db, customer, placed_order):
    """confirmation_required=True must appear in the audit entry when gate fires."""
    state = make_state(
        "cancel_order",
        {"order_id": placed_order.id, "reason": "changed_mind"},
        customer_id=customer.id,
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    entry = result["actions_taken"][0]
    assert entry["confirmation_required"] is True
    # order_id resolved from details.order_id on a confirmation_required response
    assert entry["order_id"] == placed_order.id


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

async def test_track_order_succeeds(db, customer, placed_order):
    state = make_state("track_order", {"order_id": placed_order.id}, customer_id=customer.id)
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert result["action_results"][0]["success"] is True
    assert result["action_results"][0]["status"] == "placed"


async def test_cancel_order_returns_confirmation_required_on_first_call(db, customer, placed_order):
    """First call with reason but no prior confirmation → confirmation_required."""
    state = make_state(
        "cancel_order",
        {"order_id": placed_order.id, "reason": "changed_mind"},
        customer_id=customer.id,
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert result["action_results"][0].get("confirmation_required") is True


async def test_cancel_order_succeeds_with_prior_confirmation(db, customer, placed_order):
    """Second call with prior confirmation entry → executes cancellation."""
    state = make_state(
        "cancel_order",
        {"order_id": placed_order.id, "reason": "changed_mind"},
        customer_id=customer.id,
        actions_taken=_confirmed("cancel_order", placed_order.id),
    )
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
async def returned_order(db: AsyncSession, customer):
    """Returned recently — within return window, low total to avoid high-value flag."""
    product = Product(
        id=str(uuid.uuid4()), name="Refund Widget", category="clothing",
        price=30.00, return_window_days=30, final_sale=False,
    )
    db.add(product)
    await db.flush()
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()), customer_id=customer.id,
        status="returned", total_amount=30.00,
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


async def test_risk_score_injected_from_customer_context(db, customer, returned_order):
    """action_service must inject risk_score from customer_context, not from LLM params."""
    state = make_state(
        "process_refund",
        {"order_id": returned_order.id, "reason": "changed_mind"},
        customer_id=customer.id,
        customer_context={"risk_score": 0.9},  # high risk — should trigger pending_review
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    tool_result = result["action_results"][0]
    assert tool_result["success"] is True
    assert tool_result["status"] == "pending_review"


async def test_risk_score_cannot_be_overridden_by_llm(db, customer, returned_order):
    """Even if the LLM somehow provides risk_score=0.0 in params, action_service overwrites it."""
    state = make_state(
        "process_refund",
        {"order_id": returned_order.id, "reason": "changed_mind", "risk_score": 0.0},
        customer_id=customer.id,
        customer_context={"risk_score": 0.9},  # actual risk from customer context
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    tool_result = result["action_results"][0]
    assert tool_result["success"] is True
    # The real risk_score (0.9) must have been used — not the LLM-supplied 0.0
    assert tool_result["status"] == "pending_review"


async def test_low_risk_score_from_context_approves_refund(db, customer, returned_order):
    """Low risk_score in customer_context → approved (not pending_review)."""
    state = make_state(
        "process_refund",
        {"order_id": returned_order.id, "reason": "changed_mind"},
        customer_id=customer.id,
        customer_context={"risk_score": 0.1},
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    tool_result = result["action_results"][0]
    assert tool_result["success"] is True
    assert tool_result["status"] == "approved"


async def test_missing_customer_context_defaults_to_zero_risk(db, customer, returned_order):
    """No customer_context in state → risk_score defaults to 0.0 → approved."""
    state = make_state(
        "process_refund",
        {"order_id": returned_order.id, "reason": "changed_mind"},
        customer_id=customer.id,
        customer_context={},  # no risk_score field
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    tool_result = result["action_results"][0]
    assert tool_result["success"] is True
    assert tool_result["status"] == "approved"


# ---------------------------------------------------------------------------
# initiate_return — via action_service
# ---------------------------------------------------------------------------

@pytest.fixture
async def delivered_order_for_return(db: AsyncSession, customer):
    product = Product(
        id=str(uuid.uuid4()), name="Return Widget", category="clothing",
        price=40.00, return_window_days=30, final_sale=False,
    )
    db.add(product)
    await db.flush()
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()), customer_id=customer.id,
        status="delivered", total_amount=40.00,
        delivered_at=now - timedelta(days=2),
    )
    db.add(order)
    await db.flush()
    db.add(OrderItem(
        id=str(uuid.uuid4()), order_id=order.id, product_id=product.id,
        quantity=1, price_at_purchase=40.00,
    ))
    await db.commit()
    return order


async def test_initiate_return_returns_confirmation_required_on_first_call(db, customer, delivered_order_for_return):
    state = make_state(
        "initiate_return",
        {"order_id": delivered_order_for_return.id, "reason": "changed_mind"},
        customer_id=customer.id,
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    assert result["action_results"][0].get("confirmation_required") is True


async def test_initiate_return_succeeds_with_prior_confirmation(db, customer, delivered_order_for_return):
    state = make_state(
        "initiate_return",
        {"order_id": delivered_order_for_return.id, "reason": "changed_mind"},
        customer_id=customer.id,
        actions_taken=_confirmed("initiate_return", delivered_order_for_return.id),
    )
    result = await action_service_node(state, {"configurable": {"db": db}})
    tool_result = result["action_results"][0]
    assert tool_result["success"] is True
    assert tool_result["return_label"].startswith("RETURN-")
    # action_service must record order_id and confirmation_required=False in the entry
    entry = result["actions_taken"][-1]
    assert entry["order_id"] == delivered_order_for_return.id
    assert entry["confirmation_required"] is False
