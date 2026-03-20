"""
Chat router tests.

Covers: POST /api/conversations, POST /api/chat,
        GET /api/chat/stream/{id}, GET /health
"""
import uuid
from unittest.mock import patch, AsyncMock, MagicMock
import pytest
from sqlalchemy import select

from backend.db.models import Conversation, Message, AuditLog


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

FAKE_AGENT_OUTPUT = {
    "response": "Your return window is 30 days.",
    "confidence": 0.85,
    "actions_taken": [
        {"service": "knowledge_service", "action": "search_kb", "chunks_retrieved": 3, "top_similarity": 0.85}
    ],
}


async def fake_astream(*args, **kwargs):
    yield {"conversation_agent": FAKE_AGENT_OUTPUT}


async def test_stream_unknown_conversation_returns_404(client):
    response = await client.get(f"/api/chat/stream/{uuid.uuid4()}")
    assert response.status_code == 404
    assert "Conversation not found" in response.json()["detail"]


async def test_stream_no_customer_message_returns_422(client, active_conversation):
    # active_conversation has no messages
    response = await client.get(f"/api/chat/stream/{active_conversation.id}")
    assert response.status_code == 422
    assert "No customer message" in response.json()["detail"]


@patch("backend.agents.graph.graph.astream", side_effect=fake_astream)
async def test_stream_returns_sse_content_type(mock_stream, client, conversation_with_messages):
    response = await client.get(f"/api/chat/stream/{conversation_with_messages.id}")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]


@patch("backend.agents.graph.graph.astream", side_effect=fake_astream)
async def test_stream_emits_token_events(mock_stream, client, conversation_with_messages):
    response = await client.get(f"/api/chat/stream/{conversation_with_messages.id}")
    assert "event: token" in response.text


@patch("backend.agents.graph.graph.astream", side_effect=fake_astream)
async def test_stream_emits_done_event(mock_stream, client, conversation_with_messages):
    response = await client.get(f"/api/chat/stream/{conversation_with_messages.id}")
    assert "event: done" in response.text


@patch("backend.agents.graph.graph.astream", side_effect=fake_astream)
async def test_stream_persists_agent_message(mock_stream, client, db, conversation_with_messages):
    await client.get(f"/api/chat/stream/{conversation_with_messages.id}")

    # conversation_with_messages already has one pre-existing agent message;
    # filter to the one the SSE endpoint added (identified by its content).
    result = await db.execute(
        select(Message).where(
            Message.conversation_id == conversation_with_messages.id,
            Message.role == "agent",
            Message.content == FAKE_AGENT_OUTPUT["response"],
        )
    )
    agent_msgs = result.scalars().all()
    assert len(agent_msgs) == 1
    assert agent_msgs[0].agent_type == "conversation"


@patch("backend.agents.graph.graph.astream", side_effect=fake_astream)
async def test_stream_creates_audit_log(mock_stream, client, db, conversation_with_messages):
    await client.get(f"/api/chat/stream/{conversation_with_messages.id}")

    result = await db.execute(
        select(AuditLog).where(AuditLog.conversation_id == conversation_with_messages.id)
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].agent_type == "conversation"
    assert logs[0].action == "search_kb"
    assert logs[0].confidence == FAKE_AGENT_OUTPUT["confidence"]
