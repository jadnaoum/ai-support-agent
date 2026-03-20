"""
Customer tools — context and risk scoring.
Used by the conversation agent to load customer context at turn start (Phase 3 step 5).
"""
from datetime import datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Customer, Order, Refund, Conversation


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

    risk_score = await get_risk_score(db, customer_id)

    return {
        "success": True,
        "customer_id": customer_id,
        "name": customer.name,
        "email": customer.email,
        "order_count": len(orders),
        "recent_orders": [
            {
                "order_id": str(o.id),
                "status": o.status,
                "total": float(o.total_amount),
                "placed_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders[:5]
        ],
        "risk_score": risk_score,
    }


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
