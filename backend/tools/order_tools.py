"""
Order tools — mock implementations.

Each tool validates parameters, interacts with the DB, and returns a structured
result dict. No external API calls — the value is in the registry pattern,
parameter validation, and audit trail.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Order, OrderItem, Product, Refund
from backend.tools.constants import REASON_VALUES

# Accepted reason inputs: all canonical values (map to themselves) plus
# natural-language synonyms the LLM or customer might supply.
_REASON_MAP: dict = {v: v for v in REASON_VALUES}
_REASON_MAP.update({"broken": "defective", "damaged": "defective"})

# Product categories that can never be returned, regardless of window or condition.
# These map to the non-returnable items listed in returns_and_refunds.md.
_NON_RETURNABLE_CATEGORIES = frozenset({
    "gift_cards", "digital", "personalized", "perishable", "hazardous",
})


# ── Shared validation helpers ─────────────────────────────────────────────────

def _check_cancel_eligibility_sync(order) -> dict:
    """
    Pure function — no DB calls. Returns eligibility result for a located order.
    Shape: {"eligible": bool, "reason": str|None, "details": str, "available_action": str|None}
    """
    if order.status == "cancelled":
        return {
            "eligible": False, "reason": "already_cancelled",
            "details": "This order is already cancelled.",
            "available_action": None,
        }
    if order.status == "refunded":
        return {
            "eligible": False, "reason": "refunded",
            "details": "This order has already been refunded.",
            "available_action": None,
        }
    if order.status == "returned":
        return {
            "eligible": False, "reason": "returned",
            "details": "This order has already been returned and cannot be cancelled.",
            "available_action": None,
        }
    if order.status == "shipped":
        return {
            "eligible": False, "reason": "shipped",
            "details": "This order has shipped and cannot be cancelled. You can return it for a refund once it arrives.",
            "available_action": "check_return_eligibility",
        }
    if order.status == "delivered":
        return {
            "eligible": False, "reason": "delivered",
            "details": "Delivered orders cannot be cancelled. Please request a return/refund instead.",
            "available_action": "initiate_return",
        }
    # placed — eligible
    return {
        "eligible": True, "reason": None,
        "details": "This order can be cancelled.",
        "available_action": None,
    }



def _check_return_eligibility_sync(order, products, reason=None, now=None) -> dict:
    """
    Pure function — no DB calls. Returns eligibility for initiating a return on a delivered order.
    Gate sequence for initiate_return: eligibility → reason → confirmation (reason does not affect
    eligibility here — defective claims escalate rather than routing through the return flow,
    because defective items require human judgment on replacement vs. refund).
    Shape: {"eligible": bool, "reason": str|None, "details": str, "available_action": str|None}
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Defective/damaged claims require human review — same escalation as _check_refund_eligibility_sync.
    # A customer returning a broken item expects a refund or replacement, not just a label.
    # Human judgment is needed; do not route through the standard return flow.
    if reason and reason.lower() in ("defective", "broken", "damaged"):
        return {
            "eligible": False, "reason": "requires_escalation",
            "details": (
                "Defective and damaged item claims require review by our support team. "
                "Please escalate so a team member can assess the issue and arrange the "
                "appropriate resolution (replacement or refund)."
            ),
            "available_action": None,
        }

    if order.status == "return_in_progress":
        return {
            "eligible": False, "reason": "already_in_progress",
            "details": "A return has already been initiated for this order. Check your email for the prepaid return label.",
            "available_action": None,
        }

    if order.status == "returned":
        return {
            "eligible": False, "reason": "already_returned",
            "details": "This order has already been returned.",
            "available_action": None,
        }

    if order.status == "refunded":
        return {
            "eligible": False, "reason": "already_refunded",
            "details": "This order has already been refunded.",
            "available_action": None,
        }

    if order.status != "delivered":
        return {
            "eligible": False, "reason": "wrong_status",
            "details": f"Only delivered orders can be returned. This order has status '{order.status}'.",
            "available_action": None,
        }

    if products:
        if any(p.final_sale for p in products):
            return {
                "eligible": False, "reason": "final_sale",
                "details": (
                    "One or more items in this order are marked as Final Sale and are not "
                    "eligible for returns or refunds."
                ),
                "available_action": None,
            }

        for p in products:
            if p.category in _NON_RETURNABLE_CATEGORIES:
                return {
                    "eligible": False, "reason": "non_returnable_category",
                    "details": (
                        f"This order contains a non-returnable item ({p.name}). "
                        "Gift cards, digital products, personalized items, perishable goods, "
                        "and hazardous materials cannot be returned."
                    ),
                    "available_action": None,
                }

        delivered_date = order.delivered_at or order.updated_at
        if delivered_date:
            if delivered_date.tzinfo is None:
                delivered_date = delivered_date.replace(tzinfo=timezone.utc)
            days_since = (now - delivered_date).days
            min_window = min(p.return_window_days for p in products)
            if days_since > min_window:
                return {
                    "eligible": False, "reason": "outside_return_window",
                    "details": (
                        f"The return window for this order has passed "
                        f"({min_window} days for this item type). "
                        "If your item is defective, please contact us — "
                        "defective items are handled separately."
                    ),
                    "available_action": None,
                }

    return {
        "eligible": True, "reason": None,
        "details": "This order is eligible for a return.",
        "available_action": None,
        "check_kb": True,
    }


def _has_prior_confirmation(actions_taken, tool_name: str, resolved_order_id: str) -> bool:
    """
    Returns True if actions_taken contains a confirmation_required entry for
    tool_name + resolved_order_id that has NOT been superseded by a subsequent
    successful execution of the same combination.

    Scanning forward: a success entry resets the gate, so a new confirmation
    is required for any further calls (e.g. second partial refund on same order).
    """
    found = False
    for entry in (actions_taken or []):
        if entry.get("action") != tool_name:
            continue
        if str(entry.get("order_id") or "") != resolved_order_id:
            continue
        if entry.get("confirmation_required"):
            found = True
        elif entry.get("success"):
            found = False   # success after confirmation resets the gate
    return found


# ── Agent-callable tools ──────────────────────────────────────────────────────

async def track_order(
    db: AsyncSession,
    customer_id: str,
    order_id: str = None,
    actions_taken: list = None,
) -> dict:
    """Return order status and item details. Uses most recent order if order_id is omitted."""
    if order_id:
        result = await db.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            return {"success": False, "error": f"Order {order_id} not found."}
        if str(order.customer_id) != customer_id:
            return {"success": False, "error": "That order does not belong to your account."}
    else:
        result = await db.execute(
            select(Order)
            .where(Order.customer_id == customer_id)
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        order = result.scalar_one_or_none()
        if not order:
            return {"success": False, "error": "No orders found for this account."}

    items_result = await db.execute(
        select(OrderItem, Product)
        .join(Product, OrderItem.product_id == Product.id)
        .where(OrderItem.order_id == order.id)
    )
    items = [
        {
            "product": row.Product.name,
            "quantity": row.OrderItem.quantity,
            "price": float(row.OrderItem.price_at_purchase),
        }
        for row in items_result.fetchall()
    ]

    return {
        "success": True,
        "order_id": str(order.id),
        "status": order.status,
        "total": float(order.total_amount),
        "placed_at": order.created_at.isoformat() if order.created_at else None,
        "items": items,
    }


async def check_cancel_eligibility(
    db: AsyncSession,
    customer_id: str,
    order_id: str = None,
    actions_taken: list = None,
) -> dict:
    """
    Read-only: check whether an order (or all customer orders) can be cancelled.
    Never modifies state. Returns structured eligibility result.
    """
    if order_id:
        result = await db.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            return {"success": False, "error": f"Order {order_id} not found."}
        if str(order.customer_id) != customer_id:
            return {"success": False, "error": "That order does not belong to your account."}
        check = _check_cancel_eligibility_sync(order)
        return {"success": True, "order_id": str(order.id), **check}

    # No order_id — return all cancellable orders for this customer
    result = await db.execute(
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()
    if not orders:
        return {"success": False, "error": "No orders found for this account."}

    eligible_orders = []
    for order in orders:
        check = _check_cancel_eligibility_sync(order)
        if check["eligible"]:
            eligible_orders.append({
                "order_id": str(order.id),
                "status": order.status,
                **check,
            })
    return {"success": True, "eligible_orders": eligible_orders}




async def check_return_eligibility(
    db: AsyncSession,
    customer_id: str,
    order_id: str = None,
    reason: str = None,
    actions_taken: list = None,
) -> dict:
    """
    Read-only: check whether a delivered order is eligible for a return.
    Pass reason if known — defective/damaged claims return requires_escalation.
    Never modifies state.
    """
    if order_id:
        result = await db.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            return {"success": False, "error": f"Order {order_id} not found."}
        if str(order.customer_id) != customer_id:
            return {"success": False, "error": "That order does not belong to your account."}
        items_result = await db.execute(
            select(Product)
            .join(OrderItem, OrderItem.product_id == Product.id)
            .where(OrderItem.order_id == order.id)
        )
        products = items_result.scalars().all()
        check = _check_return_eligibility_sync(order, products, reason=reason)
        return {"success": True, "order_id": str(order.id), **check}

    result = await db.execute(
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()
    if not orders:
        return {"success": False, "error": "No orders found for this account."}

    eligible_orders = []
    for order in orders:
        items_result = await db.execute(
            select(Product)
            .join(OrderItem, OrderItem.product_id == Product.id)
            .where(OrderItem.order_id == order.id)
        )
        products = items_result.scalars().all()
        check = _check_return_eligibility_sync(order, products, reason=reason)
        if check["eligible"]:
            eligible_orders.append({"order_id": str(order.id), "status": order.status, **check})
    return {"success": True, "eligible_orders": eligible_orders}


async def initiate_return(
    db: AsyncSession,
    customer_id: str,
    order_id: str = None,
    reason: str = None,
    actions_taken: list = None,
) -> dict:
    """Initiate a return. Sequence: eligibility → reason → confirmation gate → execute.

    Eligibility goes first — reason does not affect return eligibility (except defective,
    which escalates). Checking eligibility first avoids prompting for a reason on orders
    that are ineligible (wrong status, non-returnable items, etc.).
    """
    # 1. Locate order
    if order_id:
        result = await db.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            return {"success": False, "error": f"Order {order_id} not found."}
        if str(order.customer_id) != customer_id:
            return {"success": False, "error": "That order does not belong to your account."}
    else:
        result = await db.execute(
            select(Order)
            .where(Order.customer_id == customer_id)
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        order = result.scalar_one_or_none()
        if not order:
            return {"success": False, "error": "No orders found for this account."}

    resolved_order_id = str(order.id)

    # 2. Load products and check eligibility (reason-aware: defective → escalation)
    items_result = await db.execute(
        select(Product)
        .join(OrderItem, OrderItem.product_id == Product.id)
        .where(OrderItem.order_id == order.id)
    )
    products = items_result.scalars().all()

    eligibility = _check_return_eligibility_sync(order, products, reason=reason)
    if not eligibility["eligible"]:
        return {
            "success": False,
            "reason": eligibility["reason"],
            "error": eligibility["details"],
            "available_action": eligibility["available_action"],
        }

    # 3. Reason required (order is eligible — now collect reason)
    if not reason:
        return {
            "success": False,
            "reason": "reason_required",
            "details": f"Please provide a reason for the return. Valid values: {', '.join(REASON_VALUES)}.",
        }

    if reason not in _REASON_MAP:
        return {
            "success": False,
            "error": "invalid_reason",
            "message": f"Reason must be one of: {REASON_VALUES}. Received: {reason}",
        }

    # 4. Confirmation gate — first call returns details for customer to confirm
    if not _has_prior_confirmation(actions_taken, "initiate_return", resolved_order_id):
        return {
            "success": False,
            "confirmation_required": True,
            "details": {
                "order_id": resolved_order_id,
                "items": [p.name for p in products],
                "reason": reason,
            },
        }

    # 5. Execute — flip status and create refund record.
    # Orders over €50 enter pending_review (human authorises before label is issued).
    order.status = "return_in_progress"
    resolved_reason = _REASON_MAP.get(reason, "other")

    if float(order.total_amount) > 50:
        refund = Refund(
            id=str(uuid.uuid4()),
            order_id=resolved_order_id,
            customer_id=customer_id,
            amount=float(order.total_amount),
            reason=resolved_reason,
            status="pending_review",
            initiated_by="agent",
        )
        db.add(refund)
        await db.commit()
        return {
            "success": True,
            "order_id": resolved_order_id,
            "refund_id": str(refund.id),
            "pending_review": True,
        }

    label_id = f"RETURN-{str(uuid.uuid4())[:8].upper()}"
    refund = Refund(
        id=str(uuid.uuid4()),
        order_id=resolved_order_id,
        customer_id=customer_id,
        amount=float(order.total_amount),
        reason=resolved_reason,
        status="approved",
        initiated_by="agent",
    )
    db.add(refund)
    await db.commit()
    return {
        "success": True,
        "order_id": resolved_order_id,
        "return_label": label_id,
        "refund_id": str(refund.id),
        "message": (
            f"Return initiated. A prepaid return label ({label_id}) has been emailed to you. "
            "Once we receive your item, your refund will be processed."
        ),
    }


async def cancel_order(
    db: AsyncSession,
    customer_id: str,
    order_id: str = None,
    reason: str = None,
    actions_taken: list = None,
) -> dict:
    """Cancel an order. Sequence: eligibility → reason → confirmation gate → execute.

    Eligibility comes first because reason does not affect cancel eligibility — it is
    purely determined by order status. Rejecting an uncancellable order immediately
    avoids prompting the customer for a reason that will never be used.
    """
    # 1. Locate order
    if order_id:
        result = await db.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            return {"success": False, "error": f"Order {order_id} not found."}
        if str(order.customer_id) != customer_id:
            return {"success": False, "error": "That order does not belong to your account."}
    else:
        result = await db.execute(
            select(Order)
            .where(Order.customer_id == customer_id)
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        order = result.scalar_one_or_none()
        if not order:
            return {"success": False, "error": "No orders found for this account."}

    resolved_order_id = str(order.id)

    # 2. Eligibility check — reject ineligible orders before asking for reason
    eligibility = _check_cancel_eligibility_sync(order)
    if not eligibility["eligible"]:
        return {
            "success": False,
            "reason": eligibility["reason"],
            "error": eligibility["details"],
            "available_action": eligibility["available_action"],
        }

    # 3. Reason required (order is eligible — now collect reason)
    if not reason:
        return {
            "success": False,
            "reason": "reason_required",
            "details": f"Please provide a reason for the cancellation. Valid values: {', '.join(REASON_VALUES)}.",
        }

    if reason not in _REASON_MAP:
        return {
            "success": False,
            "error": "invalid_reason",
            "message": f"Reason must be one of: {REASON_VALUES}. Received: {reason}",
        }

    # 4. Confirmation gate — first call returns details for customer to confirm
    if not _has_prior_confirmation(actions_taken, "cancel_order", resolved_order_id):
        items_result = await db.execute(
            select(Product)
            .join(OrderItem, OrderItem.product_id == Product.id)
            .where(OrderItem.order_id == order.id)
        )
        products = items_result.scalars().all()
        return {
            "success": False,
            "confirmation_required": True,
            "details": {
                "order_id": resolved_order_id,
                "order_total": float(order.total_amount),
                "items": [p.name for p in products],
            },
        }

    # 5. Execute — cancel order and issue refund immediately.
    order.status = "cancelled"
    refund_amount = float(order.total_amount)
    refund = Refund(
        id=str(uuid.uuid4()),
        order_id=resolved_order_id,
        customer_id=customer_id,
        amount=refund_amount,
        reason=_REASON_MAP.get(reason, "other"),
        status="approved",
        initiated_by="agent",
    )
    db.add(refund)
    await db.commit()

    return {
        "success": True,
        "order_id": resolved_order_id,
        "refund_id": str(refund.id),
        "refund_amount": refund_amount,
        "message": (
            f"Order cancelled successfully. A refund of ${refund_amount:.2f} "
            "has been issued to your original payment method."
        ),
    }


async def get_refund_status(
    db: AsyncSession,
    customer_id: str,
    order_id: str = None,
    actions_taken: list = None,
) -> dict:
    """Read-only: look up refund records for this customer (optionally filtered by order)."""
    if order_id:
        result = await db.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            return {"success": False, "error": f"Order {order_id} not found."}
        if str(order.customer_id) != customer_id:
            return {"success": False, "error": "That order does not belong to your account."}

    query = (
        select(Refund)
        .where(Refund.customer_id == customer_id)
        .order_by(Refund.created_at.desc())
    )
    if order_id:
        query = query.where(Refund.order_id == order_id)

    result = await db.execute(query)
    refunds = result.scalars().all()

    if not refunds:
        return {"success": True, "refunds": [], "check_kb": True}

    return {
        "success": True,
        "check_kb": True,
        "refunds": [
            {
                "refund_id": str(r.id),
                "order_id": str(r.order_id),
                "amount": float(r.amount),
                "status": r.status,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in refunds
        ],
    }


# ── API-layer tools (not in TOOL_REGISTRY) ────────────────────────────────────
# Called by chat.py at the API layer and injected as read-only state.
# See .claude/rules/security.md for why these must never be added to the registry.

async def get_order_history(
    db: AsyncSession,
    customer_id: str,
) -> dict:
    """Return the customer's recent order history."""
    result = await db.execute(
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.created_at.desc())
        .limit(10)
    )
    orders = result.scalars().all()

    return {
        "success": True,
        "orders": [
            {
                "order_id": str(o.id),
                "status": o.status,
                "total": float(o.total_amount),
                "placed_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ],
    }
