"""
Seed data integrity tests.

Calls each seed helper directly with the test session so the seed can be
validated without touching the production DB.
"""
import pytest
from sqlalchemy import select, func

from backend.db.models import (
    Customer, Product, Order, OrderItem, Refund,
    Conversation, Message, AuditLog, Escalation,
)
from backend.db.seed import (
    seed_customers, seed_products, seed_orders, seed_refunds,
    seed_conversations, CUSTOMERS, PRODUCTS,
)


@pytest.fixture
async def seeded(db):
    """Run the full seed pipeline against the test DB."""
    await seed_customers(db)
    await seed_products(db)
    orders_data = await seed_orders(db)
    await seed_refunds(db, orders_data)
    await seed_conversations(db, orders_data)
    return {"orders_data": orders_data}


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

async def test_seed_creates_five_customers(db, seeded):
    result = await db.execute(select(func.count(Customer.id)))
    assert result.scalar() == 5


async def test_seed_creates_ten_products(db, seeded):
    result = await db.execute(select(func.count(Product.id)))
    assert result.scalar() == 10


async def test_seed_creates_fifteen_orders(db, seeded):
    result = await db.execute(select(func.count(Order.id)))
    assert result.scalar() == 15


async def test_seed_creates_five_conversations(db, seeded):
    result = await db.execute(select(func.count(Conversation.id)))
    assert result.scalar() == 5


async def test_seed_creates_one_refund(db, seeded):
    result = await db.execute(select(func.count(Refund.id)))
    assert result.scalar() == 1


# ---------------------------------------------------------------------------
# Customer data
# ---------------------------------------------------------------------------

async def test_seed_customers_have_expected_names(db, seeded):
    result = await db.execute(select(Customer.name))
    names = {row[0] for row in result}
    assert names == {"Sarah Chen", "Marcus Webb", "Diana Park", "James Okafor", "Lisa Tanaka"}


async def test_seed_vip_customer_has_metadata_tier(db, seeded):
    result = await db.execute(
        select(Customer).where(Customer.id == CUSTOMERS["vip"])
    )
    vip = result.scalar_one()
    assert vip.metadata_["tier"] == "vip"


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

async def test_seed_products_cover_all_categories(db, seeded):
    result = await db.execute(select(Product.category).distinct())
    categories = {row[0] for row in result}
    assert categories == {"electronics", "clothing", "home_goods", "accessories"}


async def test_seed_electronics_have_short_return_window(db, seeded):
    result = await db.execute(
        select(Product).where(Product.category == "electronics")
    )
    electronics = result.scalars().all()
    assert all(p.return_window_days == 14 for p in electronics)


async def test_seed_clothing_has_no_warranty(db, seeded):
    result = await db.execute(
        select(Product).where(Product.category == "clothing")
    )
    clothing = result.scalars().all()
    assert all(p.warranty_months is None for p in clothing)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

async def test_seed_orders_cover_all_statuses(db, seeded):
    result = await db.execute(select(Order.status).distinct())
    statuses = {row[0] for row in result}
    assert {"placed", "shipped", "delivered", "returned", "cancelled", "refunded"}.issubset(statuses)


async def test_seed_loyal_customer_has_five_orders(db, seeded):
    result = await db.execute(
        select(func.count(Order.id)).where(Order.customer_id == CUSTOMERS["loyal"])
    )
    assert result.scalar() == 5


async def test_seed_vip_customer_has_four_orders(db, seeded):
    result = await db.execute(
        select(func.count(Order.id)).where(Order.customer_id == CUSTOMERS["vip"])
    )
    assert result.scalar() == 4


async def test_seed_new_customer_has_one_order(db, seeded):
    result = await db.execute(
        select(func.count(Order.id)).where(Order.customer_id == CUSTOMERS["new"])
    )
    assert result.scalar() == 1


async def test_seed_all_orders_have_at_least_one_item(db, seeded):
    orders_result = await db.execute(select(Order.id))
    order_ids = [row[0] for row in orders_result]

    for order_id in order_ids:
        items_result = await db.execute(
            select(func.count(OrderItem.id)).where(OrderItem.order_id == order_id)
        )
        assert items_result.scalar() >= 1, f"Order {order_id} has no items"


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------

async def test_seed_refund_belongs_to_frustrated_customer(db, seeded):
    result = await db.execute(select(Refund))
    refund = result.scalar_one()
    assert refund.customer_id == CUSTOMERS["frustrated"]
    assert refund.reason == "defective"
    assert refund.status == "processed"


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

async def test_seed_conversations_cover_all_statuses(db, seeded):
    result = await db.execute(select(Conversation.status).distinct())
    statuses = {row[0] for row in result}
    assert {"active", "resolved", "escalated"}.issubset(statuses)


async def test_seed_conversations_have_messages(db, seeded):
    result = await db.execute(select(func.count(Message.id)))
    assert result.scalar() > 0


async def test_seed_escalated_conversation_has_escalation_record(db, seeded):
    esc_conv_result = await db.execute(
        select(Conversation).where(Conversation.status == "escalated")
    )
    esc_conv = esc_conv_result.scalar_one()

    esc_result = await db.execute(
        select(Escalation).where(Escalation.conversation_id == esc_conv.id)
    )
    escalation = esc_result.scalar_one()
    assert escalation.reason == "customer_requested"


async def test_seed_resolved_conversations_have_csat(db, seeded):
    result = await db.execute(
        select(Conversation).where(Conversation.status == "resolved")
    )
    resolved = result.scalars().all()
    # All resolved demo conversations have CSAT set
    assert all(c.csat_score is not None for c in resolved)


async def test_seed_active_conversation_has_no_csat(db, seeded):
    result = await db.execute(
        select(Conversation).where(Conversation.status == "active")
    )
    active = result.scalar_one()
    assert active.csat_score is None
    assert active.ended_at is None


async def test_seed_agent_messages_have_audit_logs(db, seeded):
    result = await db.execute(select(func.count(AuditLog.id)))
    assert result.scalar() > 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

async def test_seed_is_idempotent(db, seeded):
    """Running seed a second time must not create duplicates."""
    await seed_customers(db)
    await seed_products(db)

    customer_count = await db.execute(select(func.count(Customer.id)))
    product_count = await db.execute(select(func.count(Product.id)))
    assert customer_count.scalar() == 5
    assert product_count.scalar() == 10
