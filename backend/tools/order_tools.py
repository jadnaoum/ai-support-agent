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
from backend.tools.constants import REASON_VALUES, ESCALATION_REASONS

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
    Shape: {"eligible": bool, "reason": str, "available_action": str|None}
    """
    if order.status == "cancelled":
        return {"eligible": False, "reason": "already_cancelled", "available_action": None}
    if order.status == "refunded":
        return {"eligible": False, "reason": "already_refunded", "available_action": None}
    if order.status == "returned":
        return {"eligible": False, "reason": "already_returned", "available_action": None}
    if order.status == "shipped":
        return {"eligible": False, "reason": "already_shipped", "available_action": None}
    if order.status == "delivered":
        return {"eligible": False, "reason": "already_delivered", "available_action": "check_return_eligibility"}
    # placed — eligible
    return {"eligible": True, "reason": "eligible", "available_action": None}



def _check_return_eligibility_sync(order, products, reason=None, now=None) -> dict:
    """
    Pure function — no DB calls. Returns eligibility for initiating a return on a delivered order.
    Gate sequence for initiate_return: eligibility → reason → confirmation (reason does not affect
    eligibility here — defective claims escalate rather than routing through the return flow,
    because defective items require human judgment on replacement vs. refund).
    Shape: {"eligible": bool, "reason": str|None, "available_action": str|None,
            "check_kb": bool (defective only), "requires_escalation": bool (defective only)}
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Duplicate item fulfillment errors require human review — the customer was not charged for
    # the extra item, so a standard return is the wrong framing. Human judgment is needed to
    # determine whether to request a return shipment or void the fulfillment error.
    if reason and reason.lower() == "duplicate_item":
        return {"eligible": False, **_escalation_rejection("duplicate_item")}

    # Defective/damaged claims require human review — a customer returning a broken item
    # expects a refund or replacement, not just a label. Human judgment is needed.
    if reason and reason.lower() in ("defective", "broken", "damaged"):
        in_return_window = False
        in_warranty = False
        warranty_months = None
        delivered_date = order.delivered_at or order.updated_at
        if delivered_date and products:
            if delivered_date.tzinfo is None:
                delivered_date = delivered_date.replace(tzinfo=timezone.utc)
            days_since = (now - delivered_date).days
            min_window = min(p.return_window_days for p in products)
            in_return_window = days_since <= min_window
            max_warranty = max(
                (p.warranty_months for p in products if p.warranty_months),
                default=None,
            )
            if max_warranty:
                in_warranty = days_since <= max_warranty * 30
                warranty_months = max_warranty
        return {
            "eligible": False,
            **_escalation_rejection(
                "defective",
                in_return_window=in_return_window,
                in_warranty=in_warranty,
                warranty_months=warranty_months,
            ),
        }

    if order.status == "return_in_progress":
        return {"eligible": False, "reason": "already_in_progress", "available_action": None}

    if order.status == "returned":
        return {"eligible": False, "reason": "already_returned", "available_action": None}

    if order.status == "refunded":
        return {"eligible": False, "reason": "already_refunded", "available_action": None}

    if order.status != "delivered":
        return {"eligible": False, "reason": "wrong_status", "available_action": None}

    if products:
        if any(p.final_sale for p in products):
            return {"eligible": False, "reason": "final_sale", "available_action": None}

        for p in products:
            if p.category in _NON_RETURNABLE_CATEGORIES:
                return {
                    "eligible": False, "reason": "non_returnable_category",
                    "available_action": None,
                }

        delivered_date = order.delivered_at or order.updated_at
        if delivered_date:
            if delivered_date.tzinfo is None:
                delivered_date = delivered_date.replace(tzinfo=timezone.utc)
            days_since = (now - delivered_date).days
            min_window = min(p.return_window_days for p in products)
            if days_since > min_window:
                return {"eligible": False, "reason": "outside_return_window", "available_action": None}

    return {"eligible": True, "reason": "eligible", "available_action": None}


def _escalation_rejection(
    reason: str,
    in_return_window: bool = None,
    in_warranty: bool = None,
    product_name: str = None,
    category: str = None,
    warranty_months: int = None,
) -> dict:
    """
    Base escalation rejection shape — no success/eligible keys (caller adds those).
    Used both as a direct tool return (cancel_order wraps with success:False) and
    as a helper return value for eligibility sync functions (which add eligible:False).
    """
    result: dict = {
        "reason": reason,
        "available_action": None,
        "requires_escalation": True,
    }
    if reason == "defective":
        result["check_kb"] = True
        if in_return_window is not None:
            result["in_return_window"] = in_return_window
        if in_warranty is not None:
            result["in_warranty"] = in_warranty
        if warranty_months is not None:
            result["warranty_months"] = warranty_months
    if product_name is not None:
        result["product_name"] = product_name
    if category is not None:
        result["category"] = category
    return result


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
        "reason": "tracking_info",
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
    # 0. order_id is required — this tool operates on one order at a time.
    # Use check_return_eligibility (no order_id) to enumerate eligible orders first.
    if not order_id:
        return {
            "success": False,
            "reason": "order_id_required",
            "available_action": "check_return_eligibility",
        }

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
        # Spreads every eligibility field (except `eligible`) into the write
        # tool's result. Any new field added to _check_return_eligibility_sync
        # will automatically flow through to the agent via action_results.
        # If you add a field that should NOT reach the agent, filter it here.
        return {"success": False, **{k: v for k, v in eligibility.items() if k != "eligible"}}

    # 3. Reason required (order is eligible — now collect reason)
    if not reason:
        return {
            "success": False,
            "reason": "reason_required",
        }

    if reason not in _REASON_MAP:
        return {
            "success": False,
            "reason": "invalid_reason",
            "available_action": None,
        }

    # 4. Confirmation gate — first call returns details for customer to confirm
    if not _has_prior_confirmation(actions_taken, "initiate_return", resolved_order_id):
        return {
            "success": False,
            "reason": "confirmation_required",
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
            "reason": "return_pending_review",
            "order_id": resolved_order_id,
            "refund_id": str(refund.id),
            "refund_amount": float(order.total_amount),
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
        "reason": "return_initiated",
        "order_id": resolved_order_id,
        "return_label": label_id,
        "refund_id": str(refund.id),
        "refund_amount": float(order.total_amount),
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
    # 0. order_id is required — this tool operates on one order at a time.
    # Use check_cancel_eligibility (no order_id) to enumerate eligible orders first.
    if not order_id:
        return {
            "success": False,
            "reason": "order_id_required",
            "available_action": "check_cancel_eligibility",
        }

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
        # Spreads every eligibility field (except `eligible`) into the write
        # tool's result. Any new field added to _check_cancel_eligibility_sync
        # will automatically flow through to the agent via action_results.
        # If you add a field that should NOT reach the agent, filter it here.
        return {"success": False, **{k: v for k, v in eligibility.items() if k != "eligible"}}

    # 3. Reason required (order is eligible — now collect reason)
    if not reason:
        return {
            "success": False,
            "reason": "reason_required",
        }

    if reason not in _REASON_MAP:
        return {
            "success": False,
            "reason": "invalid_reason",
            "available_action": None,
        }

    resolved_reason = _REASON_MAP[reason]
    if resolved_reason in ESCALATION_REASONS:
        return {"success": False, **_escalation_rejection(resolved_reason)}

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
            "reason": "confirmation_required",
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
        "reason": "cancelled",
        "order_id": resolved_order_id,
        "refund_id": str(refund.id),
        "refund_amount": refund_amount,
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
        return {"success": True, "reason": "no_refunds", "refunds": []}

    return {
        "success": True,
        "reason": "refund_list",
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


async def check_missing_package(
    db: AsyncSession,
    customer_id: str,
    order_id: str = None,
    actions_taken: list = None,
) -> dict:
    """
    Read-only: check delivery timing for a missing package claim.
    Returns wait (< 1 business day since delivery) or file_claim (≥ 1 business day).
    """
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

    if order.status != "delivered":
        return {
            "success": False,
            "error": f"Order status is '{order.status}', not delivered. Missing package claims require a delivered order.",
        }

    items_result = await db.execute(
        select(OrderItem, Product)
        .join(Product, OrderItem.product_id == Product.id)
        .where(OrderItem.order_id == order.id)
    )
    rows = items_result.fetchall()
    product_name = rows[0].Product.name if rows else "Unknown item"

    delivered_at = order.delivered_at or order.updated_at
    now = datetime.now(timezone.utc)
    if delivered_at:
        if delivered_at.tzinfo is None:
            delivered_at = delivered_at.replace(tzinfo=timezone.utc)
        delta = now - delivered_at
        # Business days: approximate as total days minus weekends
        total_days = delta.days
        full_weeks, remainder = divmod(total_days, 7)
        business_days = full_weeks * 5
        # Count remaining days, skipping weekends
        start_weekday = delivered_at.weekday()
        for i in range(remainder):
            if (start_weekday + i) % 7 < 5:
                business_days += 1
        delivered_date_str = delivered_at.strftime("%Y-%m-%d")
    else:
        business_days = 1  # assume eligible if no date available
        delivered_date_str = "unknown"

    resolved_order_id = str(order.id)

    if business_days < 1:
        return {
            "success": True,
            "reason": "wait_for_delivery",
            "order_id": resolved_order_id,
            "product_name": product_name,
            "delivered_date": delivered_date_str,
            "business_days_since_delivery": business_days,
            "requires_escalation": False,
        }

    return {
        "success": True,
        "reason": "carrier_claim_eligible",
        "order_id": resolved_order_id,
        "product_name": product_name,
        "delivered_date": delivered_date_str,
        "business_days_since_delivery": business_days,
        "requires_escalation": True,
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
