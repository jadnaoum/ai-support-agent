"""
Schema validation tests.

Covers: constraints, defaults, FK enforcement, relationships, JSONB storage.
"""
import uuid
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.db.models import (
    Customer, Product, Order, OrderItem, Refund,
    Conversation, Message, AuditLog, Escalation,
    KBDocument, KBChunk,
)


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------

async def test_customer_creates_with_valid_data(db):
    c = Customer(id=str(uuid.uuid4()), name="Alice", email="alice@test.com")
    db.add(c)
    await db.flush()

    result = await db.execute(select(Customer).where(Customer.email == "alice@test.com"))
    fetched = result.scalar_one()
    assert fetched.name == "Alice"
    assert fetched.email == "alice@test.com"


async def test_customer_unique_email_constraint(db):
    shared_email = f"dup_{uuid.uuid4().hex[:6]}@test.com"
    db.add(Customer(id=str(uuid.uuid4()), name="First", email=shared_email))
    await db.flush()

    db.add(Customer(id=str(uuid.uuid4()), name="Second", email=shared_email))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_customer_metadata_stores_jsonb(db):
    payload = {"tier": "vip", "tags": ["loyal", "high-value"], "score": 9.5}
    c = Customer(id=str(uuid.uuid4()), name="Bob", email=f"bob_{uuid.uuid4().hex[:6]}@test.com",
                 metadata_=payload)
    db.add(c)
    await db.flush()
    await db.refresh(c)
    assert c.metadata_["tier"] == "vip"
    assert c.metadata_["tags"] == ["loyal", "high-value"]


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------

async def test_product_creates_with_valid_data(db):
    p = Product(
        id=str(uuid.uuid4()),
        name="Widget Pro",
        category="electronics",
        price=49.99,
        return_window_days=14,
        warranty_months=6,
    )
    db.add(p)
    await db.flush()
    await db.refresh(p)
    assert float(p.price) == 49.99
    assert p.return_window_days == 14


async def test_product_nullable_warranty(db):
    p = Product(
        id=str(uuid.uuid4()),
        name="Basic Tee",
        category="clothing",
        price=19.99,
        warranty_months=None,
    )
    db.add(p)
    await db.flush()
    await db.refresh(p)
    assert p.warranty_months is None


# ---------------------------------------------------------------------------
# Order + FK enforcement
# ---------------------------------------------------------------------------

async def test_order_fk_requires_valid_customer(db):
    order = Order(
        id=str(uuid.uuid4()),
        customer_id=str(uuid.uuid4()),  # non-existent customer
        status="placed",
        total_amount=100.00,
    )
    db.add(order)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_order_item_links_to_order_and_product(db, order, product):
    result = await db.execute(
        select(OrderItem).where(OrderItem.order_id == order.id)
    )
    items = result.scalars().all()
    assert len(items) == 1
    assert items[0].product_id == product.id
    assert float(items[0].price_at_purchase) == 999.99


async def test_order_item_fk_requires_valid_order(db, product):
    item = OrderItem(
        id=str(uuid.uuid4()),
        order_id=str(uuid.uuid4()),  # non-existent order
        product_id=product.id,
        quantity=1,
        price_at_purchase=9.99,
    )
    db.add(item)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

async def test_conversation_defaults(db, customer):
    conv = Conversation(id=str(uuid.uuid4()), customer_id=customer.id)
    db.add(conv)
    await db.flush()
    await db.refresh(conv)
    assert conv.status == "active"
    assert conv.messages_purged is False
    assert conv.csat_score is None
    assert conv.ended_at is None


async def test_conversation_fk_requires_valid_customer(db):
    conv = Conversation(
        id=str(uuid.uuid4()),
        customer_id=str(uuid.uuid4()),  # non-existent
    )
    db.add(conv)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

async def test_message_stores_role_and_content(db, active_conversation):
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=active_conversation.id,
        role="customer",
        content="Where is my order?",
    )
    db.add(msg)
    await db.flush()
    await db.refresh(msg)
    assert msg.role == "customer"
    assert msg.content == "Where is my order?"
    assert msg.agent_type is None


async def test_agent_message_stores_agent_type(db, active_conversation):
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=active_conversation.id,
        role="agent",
        content="Your order is shipped.",
        agent_type="knowledge",
    )
    db.add(msg)
    await db.flush()
    await db.refresh(msg)
    assert msg.agent_type == "knowledge"


async def test_message_fk_requires_valid_conversation(db):
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),  # non-existent
        role="customer",
        content="Hello",
    )
    db.add(msg)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

async def test_audit_log_stores_jsonb_fields(db, active_conversation):
    log = AuditLog(
        id=str(uuid.uuid4()),
        conversation_id=active_conversation.id,
        agent_type="knowledge",
        action="search_kb",
        input_data={"query": "return policy", "top_k": 5},
        output_data={"chunks_found": 3, "confidence": 0.87},
        routing_decision="knowledge_query",
        confidence=0.87,
    )
    db.add(log)
    await db.flush()
    await db.refresh(log)
    assert log.input_data["query"] == "return policy"
    assert log.output_data["chunks_found"] == 3
    assert log.confidence == pytest.approx(0.87)


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

async def test_escalation_links_to_conversation(db, active_conversation):
    esc = Escalation(
        id=str(uuid.uuid4()),
        conversation_id=active_conversation.id,
        reason="customer_requested",
        agent_confidence=0.42,
        context_summary="Customer asked for a manager.",
    )
    db.add(esc)
    await db.flush()
    await db.refresh(esc)
    assert esc.reason == "customer_requested"
    assert esc.conversation_id == active_conversation.id


# ---------------------------------------------------------------------------
# KBDocument + KBChunk
# ---------------------------------------------------------------------------

async def test_kb_document_creates(db):
    doc = KBDocument(
        id=str(uuid.uuid4()),
        filename="returns_policy.md",
        title="Return Policy",
        category="returns",
        version=1,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    assert doc.title == "Return Policy"
    assert doc.version == 1


async def test_kb_chunk_links_to_document(db):
    doc = KBDocument(
        id=str(uuid.uuid4()),
        filename="shipping.md",
        title="Shipping Info",
        category="shipping",
    )
    db.add(doc)
    await db.flush()

    chunk = KBChunk(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        chunk_text="Standard shipping takes 3-5 business days.",
        chunk_index=0,
    )
    db.add(chunk)
    await db.flush()
    await db.refresh(chunk)
    assert chunk.document_id == doc.id
    assert chunk.chunk_index == 0


async def test_kb_chunk_cascade_delete(db):
    doc_id = str(uuid.uuid4())
    doc = KBDocument(id=doc_id, filename="faq.md", title="FAQ", category="account")
    db.add(doc)
    await db.flush()

    for i in range(3):
        db.add(KBChunk(
            id=str(uuid.uuid4()),
            document_id=doc_id,
            chunk_text=f"Chunk {i}",
            chunk_index=i,
        ))
    await db.flush()

    await db.delete(doc)
    await db.flush()

    result = await db.execute(
        select(KBChunk).where(KBChunk.document_id == doc_id)
    )
    assert result.scalars().all() == []
