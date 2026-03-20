"""
Tests for order tools (mock implementations).
Uses real test DB — no mocking of DB calls.
"""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Customer, Order, OrderItem, Product, Refund
from backend.tools.order_tools import (
    track_order,
    cancel_order,
    process_refund,
    get_order_history,
)


@pytest.fixture
async def customer(db: AsyncSession):
    c = Customer(id=str(uuid.uuid4()), name="Test User", email=f"test-{uuid.uuid4()}@example.com")
    db.add(c)
    await db.commit()
    return c


@pytest.fixture
async def product(db: AsyncSession):
    p = Product(
        id=str(uuid.uuid4()),
        name="Test Laptop",
        category="electronics",
        price=999.00,
        return_window_days=14,
    )
    db.add(p)
    await db.commit()
    return p


@pytest.fixture
async def placed_order(db: AsyncSession, customer, product):
    order = Order(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="placed",
        total_amount=999.00,
    )
    db.add(order)
    await db.flush()
    db.add(OrderItem(
        id=str(uuid.uuid4()),
        order_id=order.id,
        product_id=product.id,
        quantity=1,
        price_at_purchase=999.00,
    ))
    await db.commit()
    return order


@pytest.fixture
async def delivered_order(db: AsyncSession, customer, product):
    order = Order(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="delivered",
        total_amount=49.99,
    )
    db.add(order)
    await db.commit()
    return order


# ---------------------------------------------------------------------------
# track_order
# ---------------------------------------------------------------------------

async def test_track_order_by_id(db, customer, placed_order):
    result = await track_order(db, customer_id=customer.id, order_id=placed_order.id)
    assert result["success"] is True
    assert result["order_id"] == placed_order.id
    assert result["status"] == "placed"
    assert len(result["items"]) == 1


async def test_track_order_most_recent(db, customer, placed_order):
    result = await track_order(db, customer_id=customer.id)
    assert result["success"] is True
    assert result["order_id"] == placed_order.id


async def test_track_order_not_found(db, customer):
    result = await track_order(db, customer_id=customer.id, order_id=str(uuid.uuid4()))
    assert result["success"] is False
    assert "not found" in result["error"].lower()


async def test_track_order_wrong_customer(db, customer, placed_order):
    other_id = str(uuid.uuid4())
    result = await track_order(db, customer_id=other_id, order_id=placed_order.id)
    assert result["success"] is False
    assert "does not belong" in result["error"].lower()


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------

async def test_cancel_placed_order(db, customer, placed_order):
    result = await cancel_order(db, customer_id=customer.id, order_id=placed_order.id)
    assert result["success"] is True
    assert "cancelled" in result["message"].lower()
    assert result["refund_amount"] == 999.0


async def test_cancel_delivered_order_is_rejected(db, customer, delivered_order):
    result = await cancel_order(db, customer_id=customer.id, order_id=delivered_order.id)
    assert result["success"] is False
    assert "delivered" in result["error"].lower()


async def test_cancel_already_cancelled_order(db, customer):
    order = Order(
        id=str(uuid.uuid4()), customer_id=customer.id,
        status="cancelled", total_amount=10.00,
    )
    db.add(order)
    await db.commit()
    result = await cancel_order(db, customer_id=customer.id, order_id=order.id)
    assert result["success"] is False
    assert "already" in result["error"].lower()


async def test_cancel_most_recent_order(db, customer, placed_order):
    result = await cancel_order(db, customer_id=customer.id)
    assert result["success"] is True


# ---------------------------------------------------------------------------
# process_refund
# ---------------------------------------------------------------------------

async def test_process_refund_delivered_order(db, customer, delivered_order):
    result = await process_refund(db, customer_id=customer.id, order_id=delivered_order.id)
    assert result["success"] is True
    assert result["amount"] == 49.99
    assert "refund_id" in result


async def test_process_refund_partial_amount(db, customer, delivered_order):
    result = await process_refund(
        db, customer_id=customer.id, order_id=delivered_order.id, amount=20.0
    )
    assert result["success"] is True
    assert result["amount"] == 20.0


async def test_process_refund_placed_order_is_rejected(db, customer, placed_order):
    result = await process_refund(db, customer_id=customer.id, order_id=placed_order.id)
    assert result["success"] is False
    assert "not eligible" in result["error"].lower()


async def test_process_refund_already_refunded(db, customer):
    order = Order(
        id=str(uuid.uuid4()), customer_id=customer.id,
        status="refunded", total_amount=10.00,
    )
    db.add(order)
    await db.commit()
    result = await process_refund(db, customer_id=customer.id, order_id=order.id)
    assert result["success"] is False
    assert "already" in result["error"].lower()


# ---------------------------------------------------------------------------
# get_order_history
# ---------------------------------------------------------------------------

async def test_get_order_history_returns_orders(db, customer, placed_order, delivered_order):
    result = await get_order_history(db, customer_id=customer.id)
    assert result["success"] is True
    assert len(result["orders"]) == 2


async def test_get_order_history_empty(db, customer):
    result = await get_order_history(db, customer_id=customer.id)
    assert result["success"] is True
    assert result["orders"] == []
