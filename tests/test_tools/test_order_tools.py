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
    process_refund,
    get_order_history,
    check_cancel_eligibility,
    check_refund_eligibility,
    check_return_eligibility,
    initiate_return,
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
    assert result["available_action"] == "check_refund_eligibility"


async def test_check_cancel_eligibility_no_order_id_returns_eligible_list(db, customer, placed_order):
    result = await check_cancel_eligibility(db, customer_id=customer.id)
    assert result["success"] is True
    assert any(o["order_id"] == placed_order.id for o in result["eligible_orders"])


# ---------------------------------------------------------------------------
# check_refund_eligibility
# ---------------------------------------------------------------------------

async def test_check_refund_eligibility_returned_order(db, customer, returned_order):
    result = await check_refund_eligibility(db, customer_id=customer.id, order_id=returned_order.id)
    assert result["success"] is True
    assert result["eligible"] is True


async def test_check_refund_eligibility_defective_reason(db, customer, returned_order):
    result = await check_refund_eligibility(
        db, customer_id=customer.id, order_id=returned_order.id, reason="defective"
    )
    assert result["success"] is True
    assert result["eligible"] is False
    assert result["reason"] == "requires_escalation"


async def test_check_refund_eligibility_delivered_order(db, customer, delivered_order):
    result = await check_refund_eligibility(db, customer_id=customer.id, order_id=delivered_order.id)
    assert result["success"] is True
    assert result["eligible"] is False
    assert result["reason"] == "return_required"
    assert result["available_action"] == "initiate_return"


# ---------------------------------------------------------------------------
# process_refund — reason gate
# ---------------------------------------------------------------------------

async def test_process_refund_no_reason_is_rejected(db, customer, returned_order):
    result = await process_refund(db, customer_id=customer.id, order_id=returned_order.id)
    assert result["success"] is False
    assert result.get("reason") == "reason_required"


# ---------------------------------------------------------------------------
# process_refund — confirmation gate
# ---------------------------------------------------------------------------

async def test_process_refund_confirmation_required_on_first_call(db, customer, returned_order):
    result = await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id, reason="changed_mind"
    )
    assert result["success"] is False
    assert result.get("confirmation_required") is True
    assert result["details"]["order_id"] == returned_order.id
    assert result["details"]["refund_amount"] == 49.99


# ---------------------------------------------------------------------------
# process_refund — basic eligibility
# ---------------------------------------------------------------------------

async def test_process_refund_returned_order(db, customer, returned_order):
    result = await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    assert result["success"] is True
    assert result["amount"] == 49.99
    assert "refund_id" in result


async def test_process_refund_partial_amount(db, customer, returned_order):
    result = await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        amount=20.0, reason="changed_mind",
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    assert result["success"] is True
    assert result["amount"] == 20.0


async def test_process_refund_delivered_order_requires_return(db, customer, delivered_order):
    """Delivered but not yet returned — must be rejected with return_required."""
    result = await process_refund(
        db, customer_id=customer.id, order_id=delivered_order.id, reason="changed_mind"
    )
    assert result["success"] is False
    assert result.get("reason") == "return_required"
    assert "returned" in result["error"].lower()


async def test_process_refund_placed_order_is_rejected(db, customer, placed_order):
    result = await process_refund(
        db, customer_id=customer.id, order_id=placed_order.id, reason="changed_mind"
    )
    assert result["success"] is False
    assert "not eligible" in result["error"].lower()


async def test_process_refund_already_refunded(db, customer):
    order = Order(
        id=str(uuid.uuid4()), customer_id=customer.id,
        status="refunded", total_amount=10.00,
    )
    db.add(order)
    await db.commit()
    result = await process_refund(
        db, customer_id=customer.id, order_id=order.id, reason="changed_mind"
    )
    assert result["success"] is False
    assert "fully refunded" in result["error"].lower()


async def test_process_refund_includes_status_in_response(db, customer, returned_order):
    result = await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    assert result["success"] is True
    assert "status" in result


async def test_process_refund_partial_leaves_order_in_returned_status(db, customer, returned_order):
    """Partial refund should not mark the order as refunded — balance remains."""
    result = await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        amount=20.0, reason="changed_mind",
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    assert result["success"] is True
    assert result["remaining_balance"] == round(49.99 - 20.0, 2)
    # Order must stay in returned so a follow-up refund is still possible
    await db.refresh(returned_order)
    assert returned_order.status == "returned"


async def test_process_refund_second_partial_uses_remaining_balance(db, customer, returned_order):
    """Second call is capped at the remaining balance, not the full order total."""
    await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        amount=20.0, reason="changed_mind",
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    result = await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        amount=99.0, reason="changed_mind",  # exceeds remaining
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    assert result["success"] is True
    assert result["amount"] == round(49.99 - 20.0, 2)  # capped at remaining
    assert result["remaining_balance"] == 0.0


async def test_process_refund_full_after_partial_marks_order_refunded(db, customer, returned_order):
    """Once remaining balance hits zero the order status flips to refunded."""
    await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        amount=20.0, reason="changed_mind",
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    await db.refresh(returned_order)
    assert returned_order.status == "refunded"


async def test_process_refund_blocks_after_balance_exhausted(db, customer, returned_order):
    """A third call after balance is fully exhausted should be rejected."""
    await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        amount=20.0, reason="changed_mind",
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    result = await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        reason="changed_mind",
    )
    assert result["success"] is False
    assert "fully refunded" in result["error"].lower()


# ---------------------------------------------------------------------------
# process_refund — final sale
# ---------------------------------------------------------------------------

async def test_process_refund_rejects_final_sale_product(db, customer, final_sale_returned_order):
    result = await process_refund(
        db, customer_id=customer.id, order_id=final_sale_returned_order.id,
        reason="changed_mind",
    )
    assert result["success"] is False
    assert "final sale" in result["error"].lower()


# ---------------------------------------------------------------------------
# process_refund — return window
# ---------------------------------------------------------------------------

async def test_process_refund_rejects_outside_return_window(db, customer, old_returned_order):
    """Electronics returned 20 days after delivery — outside the 14-day window."""
    result = await process_refund(
        db, customer_id=customer.id, order_id=old_returned_order.id,
        reason="changed_mind",
    )
    assert result["success"] is False
    assert "return window" in result["error"].lower()


async def test_process_refund_rejects_defective_reason(db, customer, old_returned_order):
    """Defective/damaged claims require human review — tool rejects with requires_escalation."""
    result = await process_refund(
        db, customer_id=customer.id, order_id=old_returned_order.id,
        reason="defective",
    )
    assert result["success"] is False
    assert result.get("reason") == "requires_escalation"


async def test_process_refund_approves_normal_refund_within_window(db, customer, returned_order):
    """Recent return, low risk, small amount — should approve."""
    result = await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        reason="changed_mind", risk_score=0.1,
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    assert result["success"] is True
    assert result["status"] == "approved"


# ---------------------------------------------------------------------------
# process_refund — pending_review
# ---------------------------------------------------------------------------

async def test_process_refund_pending_review_for_high_risk(db, customer, returned_order):
    """risk_score > 0.7 triggers pending_review."""
    result = await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        reason="changed_mind", risk_score=0.8,
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    assert result["success"] is True
    assert result["status"] == "pending_review"
    assert "under review" in result["message"].lower()


async def test_process_refund_pending_review_for_high_amount(db, customer, expensive_returned_order):
    """Refund amount > $50 triggers pending_review even with low risk."""
    result = await process_refund(
        db, customer_id=customer.id, order_id=expensive_returned_order.id,
        reason="changed_mind", risk_score=0.0,
        actions_taken=_confirmed("process_refund", expensive_returned_order.id),
    )
    assert result["success"] is True
    assert result["status"] == "pending_review"


async def test_process_refund_pending_review_message_is_informative(db, customer, returned_order):
    result = await process_refund(
        db, customer_id=customer.id, order_id=returned_order.id,
        reason="changed_mind", risk_score=0.9,
        actions_taken=_confirmed("process_refund", returned_order.id),
    )
    assert "team will follow up" in result["message"].lower()


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


# ---------------------------------------------------------------------------
# check_refund_eligibility — return_in_progress status
# ---------------------------------------------------------------------------

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


async def test_check_refund_eligibility_return_in_progress(db, customer, return_in_progress_order):
    result = await check_refund_eligibility(db, customer_id=customer.id, order_id=return_in_progress_order.id)
    assert result["success"] is True
    assert result["eligible"] is False
    assert result["reason"] == "return_in_progress"
    # No hardcoded timeline — the response should state the fact, not a number of days
    assert "3" not in result["details"] and "5" not in result["details"]


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
    result = await initiate_return(
        db, customer_id=customer.id, order_id=delivered_order.id,
        reason="changed_mind",
        actions_taken=_confirmed("initiate_return", delivered_order.id),
    )
    assert result["success"] is True
    assert "return_label" in result
    assert result["return_label"].startswith("RETURN-")
    assert result["order_id"] == delivered_order.id


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
