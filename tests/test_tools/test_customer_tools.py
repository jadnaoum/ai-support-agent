"""
Tests for customer_tools: get_customer_context and get_risk_score.
Uses real test DB via the `db` fixture from conftest.
"""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.tools.customer_tools import get_customer_context, get_risk_score
from backend.db.models import Customer, Order, Refund, Conversation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def customer(db: AsyncSession) -> Customer:
    c = Customer(
        id=str(uuid.uuid4()),
        name="Alice Test",
        email=f"alice-{uuid.uuid4()}@test.com",
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


@pytest.fixture
async def customer_with_orders(db: AsyncSession) -> Customer:
    c = Customer(
        id=str(uuid.uuid4()),
        name="Bob Orders",
        email=f"bob-{uuid.uuid4()}@test.com",
    )
    db.add(c)
    await db.flush()

    for i in range(3):
        db.add(Order(
            id=str(uuid.uuid4()),
            customer_id=c.id,
            status="delivered",
            total_amount=50.0 + i * 10,
        ))
    await db.commit()
    await db.refresh(c)
    return c


# ---------------------------------------------------------------------------
# get_customer_context
# ---------------------------------------------------------------------------

async def test_returns_success_true_for_known_customer(db, customer):
    result = await get_customer_context(db, customer.id)
    assert result["success"] is True


async def test_returns_customer_name(db, customer):
    result = await get_customer_context(db, customer.id)
    assert result["name"] == "Alice Test"


async def test_returns_customer_email(db, customer):
    result = await get_customer_context(db, customer.id)
    assert result["email"] == customer.email


async def test_returns_success_false_for_unknown_customer(db):
    result = await get_customer_context(db, str(uuid.uuid4()))
    assert result["success"] is False
    assert "error" in result


async def test_returns_recent_orders(db, customer_with_orders):
    result = await get_customer_context(db, customer_with_orders.id)
    assert result["success"] is True
    assert result["order_count"] == 3
    assert len(result["recent_orders"]) == 3


async def test_recent_orders_have_expected_fields(db, customer_with_orders):
    result = await get_customer_context(db, customer_with_orders.id)
    order = result["recent_orders"][0]
    assert "order_id" in order
    assert "status" in order
    assert "total" in order
    assert "placed_at" in order


async def test_caps_recent_orders_at_five(db: AsyncSession):
    c = Customer(id=str(uuid.uuid4()), name="Many Orders", email=f"many-{uuid.uuid4()}@test.com")
    db.add(c)
    await db.flush()
    for _ in range(8):
        db.add(Order(id=str(uuid.uuid4()), customer_id=c.id, status="delivered", total_amount=20.0))
    await db.commit()

    result = await get_customer_context(db, c.id)
    assert len(result["recent_orders"]) <= 5


async def test_includes_risk_score(db, customer):
    result = await get_customer_context(db, customer.id)
    assert "risk_score" in result
    assert 0.0 <= result["risk_score"] <= 1.0


# ---------------------------------------------------------------------------
# get_risk_score
# ---------------------------------------------------------------------------

async def test_new_customer_returns_low_risk(db, customer):
    score = await get_risk_score(db, customer.id)
    assert score == 0.1  # no orders → new customer default


async def test_customer_with_no_refunds_has_low_risk(db, customer_with_orders):
    score = await get_risk_score(db, customer_with_orders.id)
    assert score < 0.3


async def test_customer_with_refunds_has_higher_risk(db: AsyncSession, customer_with_orders):
    from sqlalchemy import select as sa_select
    # Add a refund
    orders_result = await db.execute(
        sa_select(Order).where(Order.customer_id == customer_with_orders.id)
    )
    order = orders_result.scalars().first()
    db.add(Refund(
        id=str(uuid.uuid4()),
        order_id=order.id,
        customer_id=customer_with_orders.id,
        amount=order.total_amount,
        reason="changed_mind",
        status="approved",
    ))
    await db.commit()

    score = await get_risk_score(db, customer_with_orders.id)
    assert score > 0.0


async def test_risk_score_capped_at_one(db: AsyncSession):
    c = Customer(id=str(uuid.uuid4()), name="Risky", email=f"risky-{uuid.uuid4()}@test.com")
    db.add(c)
    await db.flush()
    for _ in range(5):
        o = Order(id=str(uuid.uuid4()), customer_id=c.id, status="delivered", total_amount=100.0)
        db.add(o)
        await db.flush()
        db.add(Refund(id=str(uuid.uuid4()), order_id=o.id, customer_id=c.id, amount=100.0, reason="changed_mind", status="approved"))
    await db.commit()

    score = await get_risk_score(db, c.id)
    assert score <= 1.0


async def test_escalated_conversations_raise_risk(db: AsyncSession):
    c = Customer(id=str(uuid.uuid4()), name="Esc User", email=f"esc-{uuid.uuid4()}@test.com")
    db.add(c)
    await db.flush()
    db.add(Order(id=str(uuid.uuid4()), customer_id=c.id, status="delivered", total_amount=50.0))
    # Add escalated conversations
    for _ in range(3):
        db.add(Conversation(id=str(uuid.uuid4()), customer_id=c.id, status="escalated"))
    await db.commit()

    score = await get_risk_score(db, c.id)
    baseline_score = 0.1  # no refunds, but has orders
    assert score > baseline_score
