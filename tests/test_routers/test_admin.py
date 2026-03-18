"""
Admin router tests.

Covers: GET /api/conversations (list + filters),
        GET /api/conversations/{id} (detail),
        GET /api/metrics
"""
import uuid
import pytest
from sqlalchemy import select

from backend.db.models import Conversation, Message, AuditLog, Escalation


# ---------------------------------------------------------------------------
# GET /api/conversations — list
# ---------------------------------------------------------------------------

async def test_list_conversations_returns_list(client, active_conversation):
    response = await client.get("/api/conversations")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


async def test_list_conversations_includes_created_conversation(
    client, active_conversation
):
    response = await client.get("/api/conversations")
    ids = [c["id"] for c in response.json()]
    assert active_conversation.id in ids


async def test_list_conversations_response_shape(client, active_conversation):
    response = await client.get("/api/conversations")
    item = next(c for c in response.json() if c["id"] == active_conversation.id)
    assert set(item.keys()) == {
        "id", "customer_id", "status", "started_at",
        "ended_at", "csat_score", "message_count",
    }


async def test_list_conversations_filter_by_status(client, db, customer):
    active = Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="active")
    resolved = Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="resolved")
    db.add(active)
    db.add(resolved)
    await db.flush()

    response = await client.get("/api/conversations?status=active")
    statuses = {c["status"] for c in response.json()}
    assert statuses == {"active"}


async def test_list_conversations_filter_by_customer_id(client, db, customer):
    # Create a conversation for another customer (different ID, no DB record needed for filtering)
    my_conv = Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="active")
    db.add(my_conv)
    await db.flush()

    response = await client.get(f"/api/conversations?customer_id={customer.id}")
    ids = [c["id"] for c in response.json()]
    assert my_conv.id in ids
    # All returned conversations belong to this customer
    assert all(c["customer_id"] == customer.id for c in response.json())


async def test_list_conversations_filter_by_csat_min(client, db, customer):
    low = Conversation(id=str(uuid.uuid4()), customer_id=customer.id,
                       status="resolved", csat_score=2)
    high = Conversation(id=str(uuid.uuid4()), customer_id=customer.id,
                        status="resolved", csat_score=5)
    db.add(low)
    db.add(high)
    await db.flush()

    response = await client.get("/api/conversations?csat_min=4")
    scores = [c["csat_score"] for c in response.json() if c["csat_score"] is not None]
    assert all(s >= 4 for s in scores)


async def test_list_conversations_message_count_is_accurate(
    client, db, conversation_with_messages
):
    response = await client.get("/api/conversations")
    item = next(c for c in response.json() if c["id"] == conversation_with_messages.id)
    assert item["message_count"] == 2


async def test_list_conversations_respects_limit(client, db, customer):
    for _ in range(5):
        db.add(Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="active"))
    await db.flush()

    response = await client.get("/api/conversations?limit=2")
    assert len(response.json()) <= 2


async def test_list_conversations_csat_filter_rejects_out_of_range(client):
    response = await client.get("/api/conversations?csat_min=0")
    assert response.status_code == 422

    response = await client.get("/api/conversations?csat_max=6")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/conversations/{id} — detail
# ---------------------------------------------------------------------------

async def test_get_conversation_returns_full_detail(client, conversation_with_messages):
    response = await client.get(f"/api/conversations/{conversation_with_messages.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == conversation_with_messages.id
    assert "messages" in body
    assert "audit_logs" in body
    assert "escalations" in body


async def test_get_conversation_includes_messages(client, conversation_with_messages):
    response = await client.get(f"/api/conversations/{conversation_with_messages.id}")
    messages = response.json()["messages"]
    assert len(messages) == 2
    roles = {m["role"] for m in messages}
    assert roles == {"customer", "agent"}


async def test_get_conversation_message_shape(client, conversation_with_messages):
    response = await client.get(f"/api/conversations/{conversation_with_messages.id}")
    msg = response.json()["messages"][0]
    assert set(msg.keys()) == {"id", "role", "content", "agent_type", "created_at"}


async def test_get_conversation_includes_escalation(client, db, customer):
    conv = Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="escalated")
    db.add(conv)
    await db.flush()
    db.add(Escalation(
        id=str(uuid.uuid4()),
        conversation_id=conv.id,
        reason="low_confidence",
        agent_confidence=0.4,
    ))
    await db.flush()

    response = await client.get(f"/api/conversations/{conv.id}")
    escalations = response.json()["escalations"]
    assert len(escalations) == 1
    assert escalations[0]["reason"] == "low_confidence"


async def test_get_conversation_unknown_id_returns_404(client):
    response = await client.get(f"/api/conversations/{uuid.uuid4()}")
    assert response.status_code == 404
    assert "Conversation not found" in response.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/metrics
# ---------------------------------------------------------------------------

async def test_metrics_returns_expected_shape(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "total_conversations", "active_conversations", "resolved_conversations",
        "escalated_conversations", "escalation_rate", "avg_csat", "csat_count",
    }


async def test_metrics_counts_are_accurate(client, db, customer):
    db.add(Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="active"))
    db.add(Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="resolved"))
    db.add(Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="escalated"))
    await db.flush()

    response = await client.get("/api/metrics")
    body = response.json()
    assert body["total_conversations"] >= 3
    assert body["active_conversations"] >= 1
    assert body["resolved_conversations"] >= 1
    assert body["escalated_conversations"] >= 1


async def test_metrics_escalation_rate_is_fraction(client, db, customer):
    db.add(Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="resolved"))
    db.add(Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="escalated"))
    await db.flush()

    response = await client.get("/api/metrics")
    rate = response.json()["escalation_rate"]
    assert 0.0 <= rate <= 1.0


async def test_metrics_avg_csat_reflects_scores(client, db, customer):
    db.add(Conversation(id=str(uuid.uuid4()), customer_id=customer.id,
                        status="resolved", csat_score=4))
    db.add(Conversation(id=str(uuid.uuid4()), customer_id=customer.id,
                        status="resolved", csat_score=2))
    await db.flush()

    response = await client.get("/api/metrics")
    body = response.json()
    assert body["avg_csat"] is not None
    assert body["csat_count"] >= 2


async def test_metrics_no_conversations_returns_zero_rate(client):
    # With an empty-ish DB the rate must be a valid float
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert isinstance(response.json()["escalation_rate"], float)
