"""
Tests for the knowledge service node.
LiteLLM embedding calls are mocked — no real API calls.
A real test DB is used for pgvector search.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.knowledge_service import knowledge_service_node
from backend.db.models import KBChunk, KBDocument

FAKE_EMBEDDING = [0.1] * 1536


def make_embed_response() -> dict:
    return {"data": [{"embedding": FAKE_EMBEDDING}]}


def make_state(query: str = "What is the return policy?") -> dict:
    return {
        "messages": [{"role": "customer", "content": query}],
        "customer_id": str(uuid.uuid4()),
        "customer_context": {},
        "retrieved_context": [],
        "action_results": [],
        "confidence": 0.0,
        "requires_escalation": False,
        "escalation_reason": "",
        "actions_taken": [],
        "response": "",
        "pending_service": "knowledge",
    }


@pytest.fixture
async def kb_data(db: AsyncSession):
    """Insert a KBDocument + 3 KBChunks with known embeddings."""
    doc = KBDocument(
        id=str(uuid.uuid4()),
        filename="returns_and_refunds.md",
        title="Returns and Refunds Policy",
        category="returns",
    )
    db.add(doc)
    await db.flush()

    chunks = [
        KBChunk(
            id=str(uuid.uuid4()),
            document_id=doc.id,
            chunk_text="Standard return window is 30 days. Electronics have a 14-day return window.",
            chunk_index=0,
            embedding=[0.1] * 1536,
        ),
        KBChunk(
            id=str(uuid.uuid4()),
            document_id=doc.id,
            chunk_text="Refunds are issued to the original payment method within 3-5 business days.",
            chunk_index=1,
            embedding=[0.5] * 1536,
        ),
        KBChunk(
            id=str(uuid.uuid4()),
            document_id=doc.id,
            chunk_text="Defective items can be returned immediately for a full refund.",
            chunk_index=2,
            embedding=[0.9] * 1536,
        ),
    ]
    db.add_all(chunks)
    await db.commit()
    return doc


@patch("backend.agents.knowledge_service.litellm.aembedding", new_callable=AsyncMock)
async def test_returns_retrieved_context(mock_embed, db, kb_data):
    mock_embed.return_value = make_embed_response()
    result = await knowledge_service_node(make_state(), {"configurable": {"db": db}})
    assert "retrieved_context" in result
    assert isinstance(result["retrieved_context"], list)
    assert len(result["retrieved_context"]) > 0


@patch("backend.agents.knowledge_service.litellm.aembedding", new_callable=AsyncMock)
async def test_retrieved_context_has_expected_fields(mock_embed, db, kb_data):
    mock_embed.return_value = make_embed_response()
    result = await knowledge_service_node(make_state(), {"configurable": {"db": db}})
    chunk = result["retrieved_context"][0]
    assert "chunk_text" in chunk
    assert "title" in chunk
    assert "category" in chunk
    assert "similarity" in chunk


@patch("backend.agents.knowledge_service.litellm.aembedding", new_callable=AsyncMock)
async def test_does_not_generate_response(mock_embed, db, kb_data):
    """Knowledge service must NOT call LiteLLM completion — no customer-facing response."""
    mock_embed.return_value = make_embed_response()
    result = await knowledge_service_node(make_state(), {"configurable": {"db": db}})
    assert "response" not in result or result.get("response") is None


@patch("backend.agents.knowledge_service.litellm.aembedding", new_callable=AsyncMock)
async def test_clears_pending_service(mock_embed, db, kb_data):
    mock_embed.return_value = make_embed_response()
    result = await knowledge_service_node(make_state(), {"configurable": {"db": db}})
    assert result["pending_service"] == ""


@patch("backend.agents.knowledge_service.litellm.aembedding", new_callable=AsyncMock)
async def test_appends_actions_taken(mock_embed, db, kb_data):
    mock_embed.return_value = make_embed_response()
    result = await knowledge_service_node(make_state(), {"configurable": {"db": db}})
    assert len(result["actions_taken"]) == 1
    action = result["actions_taken"][0]
    assert action["service"] == "knowledge_service"
    assert action["action"] == "search_kb"
    assert action["chunks_retrieved"] > 0


@patch("backend.agents.knowledge_service.litellm.aembedding", new_callable=AsyncMock)
async def test_embeds_the_customer_query(mock_embed, db, kb_data):
    mock_embed.return_value = make_embed_response()
    query = "What is the return policy?"
    await knowledge_service_node(make_state(query), {"configurable": {"db": db}})
    mock_embed.assert_called_once()
    assert query in mock_embed.call_args.kwargs["input"]


@patch("backend.agents.knowledge_service.litellm.aembedding", new_callable=AsyncMock)
async def test_empty_table_returns_empty_context(mock_embed, db):
    """No KB data → retrieved_context is empty, similarity is 0."""
    mock_embed.return_value = make_embed_response()
    result = await knowledge_service_node(make_state(), {"configurable": {"db": db}})
    assert result["retrieved_context"] == []
    assert result["actions_taken"][0]["top_similarity"] == 0.0
