"""
Seed script — populates demo data for development and demo purposes.

Run from project root:
    python -m backend.db.seed
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import AsyncSessionLocal
from backend.db.models import (
    Customer, Product, Order, OrderItem, Refund,
    Conversation, Message, AuditLog, Escalation,
)


def uid() -> str:
    return str(uuid.uuid4())


def dt(days_ago: int = 0, hours_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours_ago)


# ---------------------------------------------------------------------------
# Static IDs so seed is idempotent and re-runnable
# ---------------------------------------------------------------------------
CUSTOMERS = {
    "loyal":      "11111111-0000-0000-0000-000000000001",
    "new":        "11111111-0000-0000-0000-000000000002",
    "frustrated": "11111111-0000-0000-0000-000000000003",
    "vip":        "11111111-0000-0000-0000-000000000004",
    "inactive":   "11111111-0000-0000-0000-000000000005",
}

PRODUCTS = {
    "laptop":      "22222222-0000-0000-0000-000000000001",
    "phone":       "22222222-0000-0000-0000-000000000002",
    "headphones":  "22222222-0000-0000-0000-000000000003",
    "t_shirt":     "22222222-0000-0000-0000-000000000004",
    "jeans":       "22222222-0000-0000-0000-000000000005",
    "coffee_maker":"22222222-0000-0000-0000-000000000006",
    "blender":     "22222222-0000-0000-0000-000000000007",
    "phone_case":  "22222222-0000-0000-0000-000000000008",
    "usb_hub":     "22222222-0000-0000-0000-000000000009",
    "desk_lamp":   "22222222-0000-0000-0000-000000000010",
}


async def seed_customers(db: AsyncSession) -> None:
    customers = [
        Customer(
            id=CUSTOMERS["loyal"],
            name="Sarah Chen",
            email="sarah.chen@example.com",
            metadata_={"tier": "regular", "signup_source": "organic"},
        ),
        Customer(
            id=CUSTOMERS["new"],
            name="Marcus Webb",
            email="marcus.webb@example.com",
            metadata_={"tier": "new", "signup_source": "referral"},
        ),
        Customer(
            id=CUSTOMERS["frustrated"],
            name="Diana Park",
            email="diana.park@example.com",
            metadata_={"tier": "regular", "notes": "multiple support contacts"},
        ),
        Customer(
            id=CUSTOMERS["vip"],
            name="James Okafor",
            email="james.okafor@example.com",
            metadata_={"tier": "vip", "account_manager": "support_team"},
        ),
        Customer(
            id=CUSTOMERS["inactive"],
            name="Lisa Tanaka",
            email="lisa.tanaka@example.com",
            metadata_={"tier": "regular", "last_active_days_ago": 180},
        ),
    ]
    for c in customers:
        existing = await db.get(Customer, c.id)
        if not existing:
            db.add(c)
    await db.commit()
    print(f"  Seeded {len(customers)} customers")


async def seed_products(db: AsyncSession) -> None:
    products = [
        Product(id=PRODUCTS["laptop"], name="ProBook X15 Laptop", category="electronics",
                price=1299.99, return_window_days=14, warranty_months=12,
                metadata_={"brand": "TechPro", "ram_gb": 16, "storage_gb": 512}),
        Product(id=PRODUCTS["phone"], name="Galaxy Nova 5G", category="electronics",
                price=799.99, return_window_days=14, warranty_months=12,
                metadata_={"brand": "GalaxyTech", "storage_gb": 256}),
        Product(id=PRODUCTS["headphones"], name="SoundMax Pro Headphones", category="electronics",
                price=249.99, return_window_days=14, warranty_months=6,
                metadata_={"brand": "SoundMax", "wireless": True}),
        Product(id=PRODUCTS["t_shirt"], name="Classic Cotton T-Shirt", category="clothing",
                price=29.99, return_window_days=30, warranty_months=None,
                metadata_={"material": "100% cotton", "sizes": ["XS","S","M","L","XL"]}),
        Product(id=PRODUCTS["jeans"], name="Slim Fit Denim Jeans", category="clothing",
                price=79.99, return_window_days=30, warranty_months=None,
                metadata_={"material": "denim", "fit": "slim"}),
        Product(id=PRODUCTS["coffee_maker"], name="BrewMaster 3000", category="home_goods",
                price=149.99, return_window_days=30, warranty_months=12,
                metadata_={"brand": "BrewMaster", "capacity_cups": 12}),
        Product(id=PRODUCTS["blender"], name="PowerBlend Pro", category="home_goods",
                price=89.99, return_window_days=30, warranty_months=12,
                metadata_={"brand": "PowerBlend", "watts": 1200}),
        Product(id=PRODUCTS["phone_case"], name="Rugged Phone Case", category="accessories",
                price=24.99, return_window_days=30, warranty_months=None,
                metadata_={"compatible_models": ["Galaxy Nova 5G"]}),
        Product(id=PRODUCTS["usb_hub"], name="7-Port USB-C Hub", category="accessories",
                price=49.99, return_window_days=30, warranty_months=6,
                metadata_={"ports": 7, "usb_c": True}),
        Product(id=PRODUCTS["desk_lamp"], name="LED Desk Lamp", category="home_goods",
                price=59.99, return_window_days=30, warranty_months=12,
                metadata_={"brightness_levels": 5, "color_temp": "adjustable"}),
    ]
    for p in products:
        existing = await db.get(Product, p.id)
        if not existing:
            db.add(p)
    await db.commit()
    print(f"  Seeded {len(products)} products")


async def seed_orders(db: AsyncSession) -> list[dict]:
    """Returns list of order dicts for use in conversation seeding."""
    orders_data = [
        # Loyal customer — many orders, mix of statuses
        dict(id=uid(), cid=CUSTOMERS["loyal"], status="delivered", total=1299.99, days_ago=90,
             items=[(PRODUCTS["laptop"], 1, 1299.99)]),
        dict(id=uid(), cid=CUSTOMERS["loyal"], status="delivered", total=279.98, days_ago=60,
             items=[(PRODUCTS["t_shirt"], 2, 29.99), (PRODUCTS["jeans"], 1, 79.99), (PRODUCTS["phone_case"], 1, 24.99)]),
        dict(id=uid(), cid=CUSTOMERS["loyal"], status="shipped", total=249.99, days_ago=5,
             items=[(PRODUCTS["headphones"], 1, 249.99)]),
        dict(id=uid(), cid=CUSTOMERS["loyal"], status="placed", total=59.99, days_ago=1,
             items=[(PRODUCTS["desk_lamp"], 1, 59.99)]),

        # New customer — single order
        dict(id=uid(), cid=CUSTOMERS["new"], status="placed", total=799.99, days_ago=2,
             items=[(PRODUCTS["phone"], 1, 799.99)]),

        # Frustrated customer — refunded orders
        dict(id=uid(), cid=CUSTOMERS["frustrated"], status="refunded", total=149.99, days_ago=45,
             items=[(PRODUCTS["coffee_maker"], 1, 149.99)]),
        dict(id=uid(), cid=CUSTOMERS["frustrated"], status="delivered", total=89.99, days_ago=20,
             items=[(PRODUCTS["blender"], 1, 89.99)]),
        dict(id=uid(), cid=CUSTOMERS["frustrated"], status="cancelled", total=249.99, days_ago=10,
             items=[(PRODUCTS["headphones"], 1, 249.99)]),

        # VIP customer — high value orders
        dict(id=uid(), cid=CUSTOMERS["vip"], status="delivered", total=1299.99, days_ago=120,
             items=[(PRODUCTS["laptop"], 1, 1299.99)]),
        dict(id=uid(), cid=CUSTOMERS["vip"], status="delivered", total=849.98, days_ago=45,
             items=[(PRODUCTS["phone"], 1, 799.99), (PRODUCTS["phone_case"], 1, 24.99), (PRODUCTS["usb_hub"], 1, 49.99)]),
        dict(id=uid(), cid=CUSTOMERS["vip"], status="shipped", total=299.98, days_ago=3,
             items=[(PRODUCTS["headphones"], 1, 249.99), (PRODUCTS["desk_lamp"], 1, 59.99)]),

        # Inactive customer — old delivered order
        dict(id=uid(), cid=CUSTOMERS["inactive"], status="delivered", total=79.99, days_ago=200,
             items=[(PRODUCTS["jeans"], 1, 79.99)]),
    ]

    created_order_ids = {}
    for o in orders_data:
        existing = await db.execute(select(Order).where(Order.id == o["id"]))
        if existing.scalar_one_or_none():
            created_order_ids[o["cid"]] = o["id"]
            continue

        order = Order(
            id=o["id"],
            customer_id=o["cid"],
            status=o["status"],
            total_amount=o["total"],
            created_at=dt(days_ago=o["days_ago"]),
            updated_at=dt(days_ago=o["days_ago"]),
        )
        db.add(order)
        for product_id, qty, price in o["items"]:
            db.add(OrderItem(
                id=uid(),
                order_id=o["id"],
                product_id=product_id,
                quantity=qty,
                price_at_purchase=price,
            ))
        created_order_ids[o["cid"]] = o["id"]

    await db.commit()
    print(f"  Seeded {len(orders_data)} orders")
    return orders_data


async def seed_refunds(db: AsyncSession, orders_data: list[dict]) -> None:
    refunded_orders = [o for o in orders_data if o["status"] in ("refunded", "cancelled")]
    refund_data = [
        # Frustrated customer refund history (drives high risk score)
        dict(
            order_id=next(o["id"] for o in orders_data if o["cid"] == CUSTOMERS["frustrated"] and o["status"] == "refunded"),
            customer_id=CUSTOMERS["frustrated"],
            amount=149.99,
            reason="defective",
            status="processed",
            initiated_by="customer",
            days_ago=44,
        ),
    ]
    for r in refund_data:
        db.add(Refund(
            id=uid(),
            order_id=r["order_id"],
            customer_id=r["customer_id"],
            amount=r["amount"],
            reason=r["reason"],
            status=r["status"],
            initiated_by=r["initiated_by"],
            created_at=dt(days_ago=r["days_ago"]),
            processed_at=dt(days_ago=r["days_ago"] - 1),
        ))
    await db.commit()
    print(f"  Seeded {len(refund_data)} refunds")


async def seed_conversations(db: AsyncSession, orders_data: list[dict]) -> None:
    """Seed 5 pre-generated demo conversations covering different routing paths."""

    loyal_shipped_order = next(o for o in orders_data if o["cid"] == CUSTOMERS["loyal"] and o["status"] == "shipped")
    frustrated_delivered = next(o for o in orders_data if o["cid"] == CUSTOMERS["frustrated"] and o["status"] == "delivered")

    convos = [
        # 1. Knowledge query → resolved (return policy question)
        {
            "id": uid(), "cid": CUSTOMERS["new"], "status": "resolved",
            "started": dt(days_ago=1, hours_ago=3), "ended": dt(days_ago=1, hours_ago=2),
            "csat": 5, "csat_comment": "Very helpful, quick answer!",
            "messages": [
                ("customer", "Hi, what's the return policy for electronics?", None),
                ("agent", "Electronics can be returned within 14 days of purchase in original condition. Laptops and phones have a 12-month warranty. Would you like more details?", "knowledge"),
                ("customer", "Great, thanks! That's all I needed.", None),
            ],
            "routing": "knowledge_query",
        },
        # 2. Order tracking → resolved
        {
            "id": uid(), "cid": CUSTOMERS["loyal"], "status": "resolved",
            "started": dt(days_ago=4, hours_ago=1), "ended": dt(days_ago=4),
            "csat": 4, "csat_comment": None,
            "messages": [
                ("customer", f"Can you check the status of my order?", None),
                ("agent", f"Your order for SoundMax Pro Headphones is currently shipped and on its way. Estimated delivery in 2-3 business days. Is there anything else I can help with?", "action"),
                ("customer", "Perfect, thanks!", None),
            ],
            "routing": "action_request",
            "order_id": loyal_shipped_order["id"],
        },
        # 3. Refund request → action agent → resolved
        {
            "id": uid(), "cid": CUSTOMERS["frustrated"], "status": "resolved",
            "started": dt(days_ago=19, hours_ago=5), "ended": dt(days_ago=19, hours_ago=4),
            "csat": 2, "csat_comment": "The refund took forever and the agent wasn't very helpful.",
            "messages": [
                ("customer", "My blender stopped working after 3 weeks. I want a refund.", None),
                ("agent", "I'm sorry to hear that. I can see your PowerBlend Pro order from 20 days ago. Since the blender is defective and within the 30-day return window, I'll process a refund of $89.99 for you.", "action"),
                ("customer", "OK but why did it break so fast?", None),
                ("agent", "I understand your frustration. The PowerBlend Pro comes with a 12-month warranty, so even beyond the return window you'd be covered for defects. Your refund of $89.99 has been initiated and should appear in 3-5 business days.", "action"),
            ],
            "routing": "action_request",
            "order_id": frustrated_delivered["id"],
        },
        # 4. Complex issue → escalation
        {
            "id": uid(), "cid": CUSTOMERS["frustrated"], "status": "escalated",
            "started": dt(days_ago=9, hours_ago=2), "ended": dt(days_ago=9, hours_ago=1),
            "csat": 1, "csat_comment": "Never got my issue resolved. Very disappointed.",
            "messages": [
                ("customer", "I've been trying to get a refund for 2 weeks and nobody has helped me. I want to speak to a manager right now.", None),
                ("agent", "I completely understand your frustration and I sincerely apologize for the delays. I'm escalating your case to our senior support team immediately. A manager will contact you within 2 hours.", "escalation"),
            ],
            "routing": "escalation",
            "escalation_reason": "customer_requested",
        },
        # 5. Active conversation (in progress)
        {
            "id": uid(), "cid": CUSTOMERS["vip"], "status": "active",
            "started": dt(hours_ago=1), "ended": None,
            "csat": None, "csat_comment": None,
            "messages": [
                ("customer", "Hi, I ordered headphones and a desk lamp 3 days ago. Can you give me an update?", None),
                ("agent", "Hello! I can see your order is currently shipped. Your SoundMax Pro Headphones and LED Desk Lamp are on their way and should arrive within 1-2 business days.", "action"),
                ("customer", "Great! Also, does the desk lamp come with a warranty?", None),
            ],
            "routing": "action_request",
        },
    ]

    for i, c in enumerate(convos):
        conv = Conversation(
            id=c["id"],
            customer_id=c["cid"],
            status=c["status"],
            started_at=c["started"],
            ended_at=c["ended"],
            csat_score=c["csat"],
            csat_comment=c["csat_comment"],
        )
        db.add(conv)
        await db.flush()

        msg_ids = []
        base_time = c["started"]
        for j, (role, content, agent_type) in enumerate(c["messages"]):
            msg = Message(
                id=uid(),
                conversation_id=c["id"],
                role=role,
                content=content,
                agent_type=agent_type,
                created_at=base_time + timedelta(minutes=j * 2),
            )
            db.add(msg)
            await db.flush()
            msg_ids.append(msg.id)

        # Audit log for agent messages
        for j, (role, content, agent_type) in enumerate(c["messages"]):
            if agent_type:
                db.add(AuditLog(
                    id=uid(),
                    conversation_id=c["id"],
                    message_id=msg_ids[j],
                    agent_type=agent_type,
                    action=c.get("routing", "respond"),
                    input_data={"message_index": j},
                    output_data={"response_length": len(content)},
                    routing_decision=c.get("routing"),
                    confidence=0.92 if agent_type != "escalation" else 0.45,
                    created_at=base_time + timedelta(minutes=j * 2),
                ))

        # Escalation record
        if c.get("escalation_reason"):
            db.add(Escalation(
                id=uid(),
                conversation_id=c["id"],
                reason=c["escalation_reason"],
                agent_confidence=0.45,
                context_summary="Customer requested manager after multiple unresolved refund attempts.",
                created_at=c["ended"] or c["started"],
            ))

    await db.commit()
    print(f"  Seeded {len(convos)} conversations")


async def main() -> None:
    print("Running seed script...")
    async with AsyncSessionLocal() as db:
        print("Seeding customers...")
        await seed_customers(db)
        print("Seeding products...")
        await seed_products(db)
        print("Seeding orders...")
        orders_data = await seed_orders(db)
        print("Seeding refunds...")
        await seed_refunds(db, orders_data)
        print("Seeding conversations...")
        await seed_conversations(db, orders_data)
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
