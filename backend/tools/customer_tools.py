"""
Customer tools — context and risk scoring.
Used by the conversation agent to load customer context at turn start (Phase 3 step 5).
"""
from datetime import datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.models import Customer, Order, OrderItem, Product, Refund, Conversation


async def get_customer_context(
    db: AsyncSession,
    customer_id: str,
) -> dict:
    """Return customer profile, recent orders, and computed risk score."""
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        return {"success": False, "error": "Customer not found."}

    orders_result = await db.execute(
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.created_at.desc())
        .limit(10)
    )
    orders = orders_result.scalars().all()
    recent_orders = await order_summaries(db, orders[:get_settings().recent_orders_in_context])

    risk_score = await get_risk_score(db, customer_id)

    return {
        "success": True,
        "customer_id": customer_id,
        "name": customer.name,
        "email": customer.email,
        "order_count": len(orders),
        "recent_orders": recent_orders,
        "risk_score": risk_score,
    }


async def order_summaries(db: AsyncSession, orders: list) -> "list[dict]":
    """Summaries (id, status, total, placed date, product names) for the given
    Order rows, in the order given. The shape the agent and output guard see as
    the customer's recent orders, and that tools return as candidate_orders."""
    items_by_order: "dict[str, list[dict]]" = {str(o.id): [] for o in orders}
    if orders:
        items_result = await db.execute(
            select(OrderItem.order_id, OrderItem.quantity, Product.name)
            .join(Product, OrderItem.product_id == Product.id)
            .where(OrderItem.order_id.in_([str(o.id) for o in orders]))
            .order_by(Product.name)
        )
        for order_id, quantity, name in items_result.all():
            items_by_order[str(order_id)].append({"product": name, "quantity": quantity})
    return [
        {
            "order_id": str(o.id),
            "status": o.status,
            "total": float(o.total_amount),
            "placed_at": o.created_at.isoformat() if o.created_at else None,
            "items": items_by_order[str(o.id)],
        }
        for o in orders
    ]


async def get_risk_score(
    db: AsyncSession,
    customer_id: str,
) -> float:
    """
    Compute customer risk score (0.0 = low risk, 1.0 = high risk).

    Factors:
    - Refunds requested in the last 90 days
    - Ratio of refunded orders to total orders
    - Number of escalated conversations
    """
    ninety_days_ago = datetime.utcnow() - timedelta(days=90)

    recent_refunds_result = await db.execute(
        select(func.count(Refund.id)).where(
            Refund.customer_id == customer_id,
            Refund.created_at >= ninety_days_ago,
        )
    )
    recent_refunds = recent_refunds_result.scalar() or 0

    total_orders_result = await db.execute(
        select(func.count(Order.id)).where(Order.customer_id == customer_id)
    )
    total_orders = total_orders_result.scalar() or 0

    escalations_result = await db.execute(
        select(func.count(Conversation.id)).where(
            Conversation.customer_id == customer_id,
            Conversation.status == "escalated",
        )
    )
    escalations = escalations_result.scalar() or 0

    if total_orders == 0:
        return 0.1  # new customer, low risk by default

    refund_ratio = min(recent_refunds / total_orders, 1.0)
    escalation_factor = min(escalations * 0.1, 0.3)

    return round(min(refund_ratio * 0.7 + escalation_factor, 1.0), 2)
