"""
Chat router tests.

Covers: POST /api/conversations, POST /api/chat,
        GET /api/chat/stream/{id}, GET /health
"""
import uuid
import pytest
from sqlalchemy import select

from backend.db.models import Conversation, Message


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def test_health_returns_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ai-support-agent"}


# ---------------------------------------------------------------------------
# POST /api/conversations
# ---------------------------------------------------------------------------

async def test_create_conversation_returns_id(client, customer):
    response = await client.post(
        "/api/conversations", json={"customer_id": customer.id}
    )
    assert response.status_code == 200
    body = response.json()
    assert "conversation_id" in body
    assert len(body["conversation_id"]) == 36  # UUID format


async def test_create_conversation_stores_in_db(client, db, customer):
    response = await client.post(
        "/api/conversations", json={"customer_id": customer.id}
    )
    conv_id = response.json()["conversation_id"]

    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one()
    assert conv.customer_id == customer.id
    assert conv.status == "active"


async def test_create_conversation_unknown_customer_returns_404(client):
    response = await client.post(
        "/api/conversations", json={"customer_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404
    assert "Customer not found" in response.json()["detail"]


async def test_create_conversation_missing_field_returns_422(client):
    response = await client.post("/api/conversations", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/chat
# ---------------------------------------------------------------------------

async def test_send_message_returns_conversation_and_message_id(
    client, active_conversation, customer
):
    response = await client.post("/api/chat", json={
        "conversation_id": active_conversation.id,
        "customer_id": customer.id,
        "message": "Where is my order?",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == active_conversation.id
    assert "message_id" in body


async def test_send_message_persists_to_db(client, db, active_conversation, customer):
    await client.post("/api/chat", json={
        "conversation_id": active_conversation.id,
        "customer_id": customer.id,
        "message": "I need a refund",
    })

    result = await db.execute(
        select(Message).where(Message.conversation_id == active_conversation.id)
    )
    messages = result.scalars().all()
    assert len(messages) == 1
    assert messages[0].role == "customer"
    assert messages[0].content == "I need a refund"


async def test_send_message_unknown_conversation_returns_404(client, customer):
    response = await client.post("/api/chat", json={
        "conversation_id": str(uuid.uuid4()),
        "customer_id": customer.id,
        "message": "Hello",
    })
    assert response.status_code == 404
    assert "Conversation not found" in response.json()["detail"]


async def test_send_message_wrong_customer_returns_403(
    client, db, active_conversation
):
    other_customer_id = str(uuid.uuid4())
    response = await client.post("/api/chat", json={
        "conversation_id": active_conversation.id,
        "customer_id": other_customer_id,
        "message": "Hello",
    })
    assert response.status_code == 403
    assert "does not own" in response.json()["detail"]


async def test_send_multiple_messages_all_stored(client, db, active_conversation, customer):
    for text in ["First message", "Second message", "Third message"]:
        await client.post("/api/chat", json={
            "conversation_id": active_conversation.id,
            "customer_id": customer.id,
            "message": text,
        })

    result = await db.execute(
        select(Message).where(Message.conversation_id == active_conversation.id)
    )
    assert len(result.scalars().all()) == 3


async def test_send_message_missing_fields_returns_422(client):
    response = await client.post("/api/chat", json={"message": "Hello"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/chat/stream/{conversation_id}
# ---------------------------------------------------------------------------

async def test_stream_existing_conversation_returns_501(client, active_conversation):
    response = await client.get(f"/api/chat/stream/{active_conversation.id}")
    assert response.status_code == 501
    assert "Phase 2" in response.json()["detail"]


async def test_stream_unknown_conversation_returns_404(client):
    response = await client.get(f"/api/chat/stream/{uuid.uuid4()}")
    assert response.status_code == 404
    assert "Conversation not found" in response.json()["detail"]
