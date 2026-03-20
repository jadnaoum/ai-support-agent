"""
Order tools — mock implementations.

Each tool validates parameters, interacts with the DB, and returns a structured
result dict. No external API calls — the value is in the registry pattern,
parameter validation, and audit trail.
"""
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Order, OrderItem, Product, Refund


async def track_order(
    db: AsyncSession,
    customer_id: str,
    order_id: str = None,
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


async def cancel_order(
    db: AsyncSession,
    customer_id: str,
    order_id: str = None,
    reason: str = "customer_requested",
) -> dict:
    """Cancel an order if its status allows it."""
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

    if order.status in ("cancelled", "refunded"):
        return {"success": False, "error": f"This order is already {order.status}."}
    if order.status == "delivered":
        return {
            "success": False,
            "error": "Delivered orders cannot be cancelled. Please request a return/refund instead.",
        }

    order.status = "cancelled"
    await db.commit()

    return {
        "success": True,
        "order_id": str(order.id),
        "message": (
            f"Order cancelled successfully. A refund of ${float(order.total_amount):.2f} "
            "will be processed to your original payment method within 3–5 business days."
        ),
        "refund_amount": float(order.total_amount),
    }


async def process_refund(
    db: AsyncSession,
    customer_id: str,
    order_id: str = None,
    amount: float = None,
    reason: str = "other",
) -> dict:
    """Initiate a refund for an eligible order."""
    # Map free-text reason to valid DB enum values
    reason_map = {
        "defective": "defective",
        "broken": "defective",
        "damaged": "defective",
        "wrong": "wrong_item",
        "wrong_item": "wrong_item",
        "changed_mind": "changed_mind",
        "late": "late_delivery",
        "late_delivery": "late_delivery",
    }
    db_reason = reason_map.get(reason.lower(), "other")

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

    if order.status == "refunded":
        return {"success": False, "error": "This order has already been refunded."}
    if order.status not in ("delivered", "cancelled"):
        return {
            "success": False,
            "error": f"Orders with status '{order.status}' are not eligible for a refund yet.",
        }

    refund_amount = (
        min(float(amount), float(order.total_amount))
        if amount and float(amount) > 0
        else float(order.total_amount)
    )

    refund = Refund(
        id=str(uuid.uuid4()),
        order_id=str(order.id),
        customer_id=customer_id,
        amount=refund_amount,
        reason=db_reason,
        status="approved",
        initiated_by="agent",
    )
    db.add(refund)
    order.status = "refunded"
    await db.commit()

    return {
        "success": True,
        "order_id": str(order.id),
        "refund_id": str(refund.id),
        "amount": refund_amount,
        "message": (
            f"Refund of ${refund_amount:.2f} approved. It will appear on your original "
            "payment method within 3–5 business days."
        ),
    }


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
