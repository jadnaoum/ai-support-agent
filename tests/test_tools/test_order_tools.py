"""
Tests for order tools (mock implementations).
Uses real test DB — no mocking of DB calls.
"""
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Customer, Order, OrderItem, Product, Refund
from backend.tools.order_tools import (
    track_order,
    cancel_order,
    get_order_history,
    check_cancel_eligibility,
    check_return_eligibility,
    initiate_return,
    get_refund_status,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

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
        final_sale=False,
    )
    db.add(p)
    await db.commit()
    return p


@pytest.fixture
async def clothing_product(db: AsyncSession):
    p = Product(
        id=str(uuid.uuid4()),
        name="Test T-Shirt",
        category="clothing",
        price=29.99,
        return_window_days=30,
        final_sale=False,
    )
    db.add(p)
    await db.commit()
    return p


@pytest.fixture
async def final_sale_product(db: AsyncSession):
    p = Product(
        id=str(uuid.uuid4()),
        name="Clearance Item",
        category="accessories",
        price=9.99,
        return_window_days=30,
        final_sale=True,
    )
    db.add(p)
    await db.commit()
    return p


def _add_item(order, product):
    return OrderItem(
        id=str(uuid.uuid4()),
        order_id=order.id,
        product_id=product.id,
        quantity=1,
        price_at_purchase=float(product.price),
    )


def _confirmed(tool_name: str, order_id: str) -> list:
    """Return an actions_taken list that satisfies the confirmation gate for one call."""
    return [{"action": tool_name, "order_id": order_id, "confirmation_required": True}]


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
    db.add(_add_item(order, product))
    await db.commit()
    return order


@pytest.fixture
async def shipped_order(db: AsyncSession, customer, product):
    order = Order(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="shipped",
        total_amount=999.00,
    )
    db.add(order)
    await db.flush()
    db.add(_add_item(order, product))
    await db.commit()
    return order


@pytest.fixture
async def delivered_order(db: AsyncSession, customer, product):
    """Delivered recently — NOT yet returned; refund should be rejected."""
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="delivered",
        total_amount=49.99,
        delivered_at=now - timedelta(days=3),
    )
    db.add(order)
    await db.flush()
    db.add(_add_item(order, product))
    await db.commit()
    return order


@pytest.fixture
async def returned_order(db: AsyncSession, customer, product):
    """Item returned within the 14-day electronics window — eligible for refund."""
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="returned",
        total_amount=49.99,
        delivered_at=now - timedelta(days=3),
    )
    db.add(order)
    await db.flush()
    db.add(_add_item(order, product))
    await db.commit()
    return order


@pytest.fixture
async def old_returned_order(db: AsyncSession, customer, product):
    """Electronics item returned 20 days after delivery — outside the 14-day window."""
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="returned",
        total_amount=49.99,
        delivered_at=now - timedelta(days=20),
    )
    db.add(order)
    await db.flush()
    db.add(_add_item(order, product))
    await db.commit()
    return order


@pytest.fixture
async def expensive_returned_order(db: AsyncSession, customer, clothing_product):
    """Clothing item returned recently, total > $50 — triggers pending_review."""
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="returned",
        total_amount=199.00,
        delivered_at=now - timedelta(days=3),
    )
    db.add(order)
    await db.flush()
    db.add(_add_item(order, clothing_product))
    await db.commit()
    return order


@pytest.fixture
async def final_sale_returned_order(db: AsyncSession, customer, final_sale_product):
    """Returned order containing a final_sale product."""
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()),
        customer_id=customer.id,
        status="returned",
        total_amount=9.99,
        delivered_at=now - timedelta(days=2),
    )
    db.add(order)
    await db.flush()
    db.add(_add_item(order, final_sale_product))
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
# cancel_order — reason gate
# ---------------------------------------------------------------------------

async def test_cancel_order_no_reason_is_rejected(db, customer, placed_order):
    result = await cancel_order(db, customer_id=customer.id, order_id=placed_order.id)
    assert result["success"] is False
    assert result.get("reason") == "reason_required"


# ---------------------------------------------------------------------------
# cancel_order — confirmation gate
# ---------------------------------------------------------------------------

async def test_cancel_order_confirmation_required_on_first_call(db, customer, placed_order):
    result = await cancel_order(
        db, customer_id=customer.id, order_id=placed_order.id, reason="changed_mind"
    )
    assert result["success"] is False
    assert result.get("confirmation_required") is True
    assert result["details"]["order_id"] == placed_order.id


async def test_cancel_placed_order(db, customer, placed_order):
    result = await cancel_order(
        db, customer_id=customer.id, order_id=placed_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("cancel_order", placed_order.id),
    )
    assert result["success"] is True
    assert "cancelled" in result["message"].lower()
    assert result["refund_amount"] == 999.0


async def test_cancel_shipped_order_is_rejected(db, customer, shipped_order):
    # No reason needed — eligibility is checked first; ineligible orders are rejected immediately
    result = await cancel_order(db, customer_id=customer.id, order_id=shipped_order.id)
    assert result["success"] is False
    assert "shipped" in result["error"].lower()
    assert "return" in result["error"].lower()


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
    result = await cancel_order(
        db, customer_id=customer.id,
        reason="changed_mind",
        actions_taken=_confirmed("cancel_order", placed_order.id),
    )
    assert result["success"] is True


# ---------------------------------------------------------------------------
# check_cancel_eligibility
# ---------------------------------------------------------------------------

async def test_check_cancel_eligibility_placed_order(db, customer, placed_order):
    result = await check_cancel_eligibility(db, customer_id=customer.id, order_id=placed_order.id)
    assert result["success"] is True
    assert result["eligible"] is True
    assert result["order_id"] == placed_order.id


async def test_check_cancel_eligibility_shipped_order(db, customer, shipped_order):
    result = await check_cancel_eligibility(db, customer_id=customer.id, order_id=shipped_order.id)
    assert result["success"] is True
    assert result["eligible"] is False
    assert result["reason"] == "shipped"
    assert result["available_action"] == "check_return_eligibility"


async def test_check_cancel_eligibility_no_order_id_returns_eligible_list(db, customer, placed_order):
    result = await check_cancel_eligibility(db, customer_id=customer.id)
    assert result["success"] is True
    assert any(o["order_id"] == placed_order.id for o in result["eligible_orders"])




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


# ---------------------------------------------------------------------------
# check_return_eligibility
# ---------------------------------------------------------------------------

async def test_check_return_eligibility_delivered_order(db, customer, delivered_order):
    result = await check_return_eligibility(db, customer_id=customer.id, order_id=delivered_order.id)
    assert result["success"] is True
    assert result["eligible"] is True


async def test_check_return_eligibility_defective_reason_escalates(db, customer, delivered_order):
    result = await check_return_eligibility(
        db, customer_id=customer.id, order_id=delivered_order.id, reason="defective"
    )
    assert result["success"] is True
    assert result["eligible"] is False
    assert result["reason"] == "requires_escalation"


async def test_check_return_eligibility_returned_order(db, customer, returned_order):
    result = await check_return_eligibility(db, customer_id=customer.id, order_id=returned_order.id)
    assert result["success"] is True
    assert result["eligible"] is False
    assert result["reason"] == "already_returned"


@pytest.fixture
async def delivered_final_sale_order(db: AsyncSession, customer, final_sale_product):
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()), customer_id=customer.id,
        status="delivered", total_amount=9.99,
        delivered_at=now - timedelta(days=2),
    )
    db.add(order)
    await db.flush()
    db.add(_add_item(order, final_sale_product))
    await db.commit()
    return order


async def test_check_return_eligibility_final_sale_delivered(db, customer, delivered_final_sale_order):
    result = await check_return_eligibility(db, customer_id=customer.id, order_id=delivered_final_sale_order.id)
    assert result["success"] is True
    assert result["eligible"] is False
    assert result["reason"] == "final_sale"


@pytest.fixture
async def return_in_progress_order(db: AsyncSession, customer, product):
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()), customer_id=customer.id,
        status="return_in_progress", total_amount=49.99,
        delivered_at=now - timedelta(days=2),
    )
    db.add(order)
    await db.flush()
    db.add(_add_item(order, product))
    await db.commit()
    return order


async def test_check_return_eligibility_return_in_progress(db, customer, return_in_progress_order):
    result = await check_return_eligibility(db, customer_id=customer.id, order_id=return_in_progress_order.id)
    assert result["success"] is True
    assert result["eligible"] is False
    assert result["reason"] == "already_in_progress"


# ---------------------------------------------------------------------------
# initiate_return — reason gate
# ---------------------------------------------------------------------------

async def test_initiate_return_no_reason_is_rejected(db, customer, delivered_order):
    result = await initiate_return(db, customer_id=customer.id, order_id=delivered_order.id)
    assert result["success"] is False
    assert result.get("reason") == "reason_required"


async def test_initiate_return_defective_reason_escalates(db, customer, delivered_order):
    result = await initiate_return(
        db, customer_id=customer.id, order_id=delivered_order.id, reason="defective"
    )
    assert result["success"] is False
    assert result.get("reason") == "requires_escalation"


# ---------------------------------------------------------------------------
# initiate_return — confirmation gate
# ---------------------------------------------------------------------------

async def test_initiate_return_confirmation_required_on_first_call(db, customer, delivered_order):
    result = await initiate_return(
        db, customer_id=customer.id, order_id=delivered_order.id, reason="changed_mind"
    )
    assert result["success"] is False
    assert result.get("confirmation_required") is True
    assert result["details"]["order_id"] == delivered_order.id


async def test_initiate_return_succeeds_with_prior_confirmation(db, customer, delivered_order):
    """Small order (≤50) — issues label and creates approved refund record."""
    result = await initiate_return(
        db, customer_id=customer.id, order_id=delivered_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("initiate_return", delivered_order.id),
    )
    assert result["success"] is True
    assert "return_label" in result
    assert result["return_label"].startswith("RETURN-")
    assert result["order_id"] == delivered_order.id
    assert "refund_id" in result


async def test_initiate_return_flips_status_to_return_in_progress(db, customer, delivered_order):
    await initiate_return(
        db, customer_id=customer.id, order_id=delivered_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("initiate_return", delivered_order.id),
    )
    await db.refresh(delivered_order)
    assert delivered_order.status == "return_in_progress"


async def test_initiate_return_blocks_if_already_in_progress(db, customer, return_in_progress_order):
    result = await initiate_return(
        db, customer_id=customer.id, order_id=return_in_progress_order.id, reason="changed_mind"
    )
    assert result["success"] is False
    assert result.get("reason") == "already_in_progress"


async def test_initiate_return_placed_order_is_rejected(db, customer, placed_order):
    result = await initiate_return(
        db, customer_id=customer.id, order_id=placed_order.id, reason="changed_mind"
    )
    assert result["success"] is False
    assert result.get("reason") == "wrong_status"


# ---------------------------------------------------------------------------
# initiate_return — pending_review for orders > €50
# ---------------------------------------------------------------------------

@pytest.fixture
async def expensive_delivered_order(db: AsyncSession, customer, clothing_product):
    """Clothing item recently delivered, total > €50 — triggers pending_review on return."""
    now = datetime.now(timezone.utc)
    order = Order(
        id=str(uuid.uuid4()), customer_id=customer.id,
        status="delivered", total_amount=199.00,
        delivered_at=now - timedelta(days=3),
    )
    db.add(order)
    await db.flush()
    db.add(_add_item(order, clothing_product))
    await db.commit()
    return order


async def test_initiate_return_pending_review_for_large_order(db, customer, expensive_delivered_order):
    """Orders > €50 create a pending_review refund record and return no label."""
    result = await initiate_return(
        db, customer_id=customer.id, order_id=expensive_delivered_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("initiate_return", expensive_delivered_order.id),
    )
    assert result["success"] is True
    assert result.get("pending_review") is True
    assert "return_label" not in result
    assert "refund_id" in result


async def test_initiate_return_creates_approved_refund_for_small_order(db, customer, delivered_order):
    """Orders ≤ €50 create an approved refund record."""
    from sqlalchemy import select as sa_select
    result = await initiate_return(
        db, customer_id=customer.id, order_id=delivered_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("initiate_return", delivered_order.id),
    )
    assert result["success"] is True
    refund_result = await db.execute(
        sa_select(Refund).where(Refund.id == result["refund_id"])
    )
    refund = refund_result.scalar_one()
    assert refund.status == "approved"
    assert float(refund.amount) == 49.99


# ---------------------------------------------------------------------------
# cancel_order — auto-refund
# ---------------------------------------------------------------------------

async def test_cancel_order_creates_refund_record(db, customer, placed_order):
    """Cancellation should create an approved Refund record immediately."""
    from sqlalchemy import select as sa_select
    result = await cancel_order(
        db, customer_id=customer.id, order_id=placed_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("cancel_order", placed_order.id),
    )
    assert result["success"] is True
    assert "refund_id" in result
    assert result["refund_amount"] == 999.00
    refund_result = await db.execute(
        sa_select(Refund).where(Refund.id == result["refund_id"])
    )
    refund = refund_result.scalar_one()
    assert refund.status == "approved"
    assert float(refund.amount) == 999.00


# ---------------------------------------------------------------------------
# get_refund_status
# ---------------------------------------------------------------------------

async def test_get_refund_status_no_refunds(db, customer):
    result = await get_refund_status(db, customer_id=customer.id)
    assert result["success"] is True
    assert result["refunds"] == []
    assert result.get("check_kb") is True


async def test_get_refund_status_finds_refund_after_cancellation(db, customer, placed_order):
    """Cancel an order then verify get_refund_status returns the created refund."""
    cancel_result = await cancel_order(
        db, customer_id=customer.id, order_id=placed_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("cancel_order", placed_order.id),
    )
    assert cancel_result["success"] is True

    result = await get_refund_status(db, customer_id=customer.id, order_id=placed_order.id)
    assert result["success"] is True
    assert len(result["refunds"]) == 1
    assert result["refunds"][0]["status"] == "approved"
    assert result["refunds"][0]["amount"] == 999.00


async def test_get_refund_status_filters_by_order_id(db, customer, placed_order):
    """order_id filter should return only refunds for that order."""
    await cancel_order(
        db, customer_id=customer.id, order_id=placed_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("cancel_order", placed_order.id),
    )
    wrong_order_id = str(uuid.uuid4())
    # Create a second order for this customer
    other_order = Order(
        id=wrong_order_id, customer_id=customer.id,
        status="placed", total_amount=10.00,
    )
    db.add(other_order)
    await db.commit()

    result = await get_refund_status(db, customer_id=customer.id, order_id=wrong_order_id)
    assert result["success"] is True
    assert result["refunds"] == []
