"""
Webhooks router tests.

Covers: POST /api/csat — happy path, error cases, validation
"""
import uuid
import pytest
from sqlalchemy import select

from backend.db.models import Conversation


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

async def test_csat_stores_score(client, db, resolved_conversation):
    response = await client.post("/api/csat", json={
        "conversation_id": resolved_conversation.id,
        "score": 5,
    })
    assert response.status_code == 200

    await db.refresh(resolved_conversation)
    assert resolved_conversation.csat_score == 5
    assert resolved_conversation.csat_comment is None


async def test_csat_stores_comment(client, db, resolved_conversation):
    response = await client.post("/api/csat", json={
        "conversation_id": resolved_conversation.id,
        "score": 3,
        "comment": "Average experience.",
    })
    assert response.status_code == 200

    await db.refresh(resolved_conversation)
    assert resolved_conversation.csat_score == 3
    assert resolved_conversation.csat_comment == "Average experience."


async def test_csat_response_shape(client, resolved_conversation):
    response = await client.post("/api/csat", json={
        "conversation_id": resolved_conversation.id,
        "score": 4,
    })
    body = response.json()
    assert body["conversation_id"] == resolved_conversation.id
    assert body["score"] == 4
    assert "message" in body


async def test_csat_accepts_all_valid_scores(client, db, customer):
    for score in [1, 2, 3, 4, 5]:
        conv = Conversation(
            id=str(uuid.uuid4()),
            customer_id=customer.id,
            status="resolved",
        )
        db.add(conv)
        await db.flush()

        response = await client.post("/api/csat", json={
            "conversation_id": conv.id,
            "score": score,
        })
        assert response.status_code == 200, f"Score {score} should be valid"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

async def test_csat_unknown_conversation_returns_404(client):
    response = await client.post("/api/csat", json={
        "conversation_id": str(uuid.uuid4()),
        "score": 4,
    })
    assert response.status_code == 404
    assert "Conversation not found" in response.json()["detail"]


async def test_csat_active_conversation_returns_400(client, active_conversation):
    response = await client.post("/api/csat", json={
        "conversation_id": active_conversation.id,
        "score": 5,
    })
    assert response.status_code == 400
    assert "active" in response.json()["detail"].lower()


async def test_csat_duplicate_submission_returns_409(client, resolved_conversation):
    await client.post("/api/csat", json={
        "conversation_id": resolved_conversation.id,
        "score": 4,
    })
    response = await client.post("/api/csat", json={
        "conversation_id": resolved_conversation.id,
        "score": 5,
    })
    assert response.status_code == 409
    assert "already submitted" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

async def test_csat_score_below_range_returns_422(client, resolved_conversation):
    response = await client.post("/api/csat", json={
        "conversation_id": resolved_conversation.id,
        "score": 0,
    })
    assert response.status_code == 422


async def test_csat_score_above_range_returns_422(client, resolved_conversation):
    response = await client.post("/api/csat", json={
        "conversation_id": resolved_conversation.id,
        "score": 6,
    })
    assert response.status_code == 422


async def test_csat_missing_score_returns_422(client, resolved_conversation):
    response = await client.post("/api/csat", json={
        "conversation_id": resolved_conversation.id,
    })
    assert response.status_code == 422


async def test_csat_missing_conversation_id_returns_422(client):
    response = await client.post("/api/csat", json={"score": 4})
    assert response.status_code == 422
