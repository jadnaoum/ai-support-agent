"""
Tests for the knowledge agent node.
LiteLLM calls are mocked — no real API calls are made.
A real test DB is used for pgvector search.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.knowledge_agent import knowledge_agent_node
from backend.db.models import KBChunk, KBDocument

FAKE_EMBEDDING = [0.1] * 1536
FAKE_RESPONSE = "Your return window is 30 days for most items and 14 days for electronics."


def make_embed_response(n: int = 1) -> dict:
    return {"data": [{"embedding": FAKE_EMBEDDING} for _ in range(n)]}


def make_completion_response(content: str = FAKE_RESPONSE) -> MagicMock:
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = content
    return mock


def make_state(query: str = "What is the return policy?") -> dict:
    return {
        "messages": [{"role": "customer", "content": query}],
        "customer_id": str(uuid.uuid4()),
        "customer_context": {},
        "current_intent": "knowledge_query",
        "routing_decision": "hardcoded_knowledge_phase2",
        "confidence": 0.0,
        "response": "",
        "requires_escalation": False,
        "actions_taken": [],
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


@patch("backend.agents.knowledge_agent.litellm.acompletion", new_callable=AsyncMock)
@patch("backend.agents.knowledge_agent.litellm.aembedding", new_callable=AsyncMock)
async def test_returns_response_text(mock_embed, mock_complete, db, kb_data):
    mock_embed.return_value = make_embed_response()
    mock_complete.return_value = make_completion_response()

    result = await knowledge_agent_node(make_state(), {"configurable": {"db": db}})

    assert "response" in result
    assert isinstance(result["response"], str)
    assert len(result["response"]) > 0


@patch("backend.agents.knowledge_agent.litellm.acompletion", new_callable=AsyncMock)
@patch("backend.agents.knowledge_agent.litellm.aembedding", new_callable=AsyncMock)
async def test_sets_confidence(mock_embed, mock_complete, db, kb_data):
    mock_embed.return_value = make_embed_response()
    mock_complete.return_value = make_completion_response()

    result = await knowledge_agent_node(make_state(), {"configurable": {"db": db}})

    assert "confidence" in result
    assert 0.0 <= result["confidence"] <= 1.0


@patch("backend.agents.knowledge_agent.litellm.acompletion", new_callable=AsyncMock)
@patch("backend.agents.knowledge_agent.litellm.aembedding", new_callable=AsyncMock)
async def test_appends_actions_taken(mock_embed, mock_complete, db, kb_data):
    mock_embed.return_value = make_embed_response()
    mock_complete.return_value = make_completion_response()

    result = await knowledge_agent_node(make_state(), {"configurable": {"db": db}})

    assert "actions_taken" in result
    assert len(result["actions_taken"]) == 1
    assert result["actions_taken"][0]["action"] == "search_kb"
    assert result["actions_taken"][0]["agent"] == "knowledge"


@patch("backend.agents.knowledge_agent.litellm.acompletion", new_callable=AsyncMock)
@patch("backend.agents.knowledge_agent.litellm.aembedding", new_callable=AsyncMock)
async def test_embeds_the_customer_query(mock_embed, mock_complete, db, kb_data):
    mock_embed.return_value = make_embed_response()
    mock_complete.return_value = make_completion_response()

    query = "What is the return policy?"
    await knowledge_agent_node(make_state(query), {"configurable": {"db": db}})

    mock_embed.assert_called_once()
    call_input = mock_embed.call_args.kwargs["input"]
    assert query in call_input


@patch("backend.agents.knowledge_agent.litellm.acompletion", new_callable=AsyncMock)
@patch("backend.agents.knowledge_agent.litellm.aembedding", new_callable=AsyncMock)
async def test_llm_called_with_kb_context_in_system_prompt(mock_embed, mock_complete, db, kb_data):
    mock_embed.return_value = make_embed_response()
    mock_complete.return_value = make_completion_response()

    await knowledge_agent_node(make_state(), {"configurable": {"db": db}})

    mock_complete.assert_called_once()
    messages = mock_complete.call_args.kwargs["messages"]
    system_msg = messages[0]
    assert system_msg["role"] == "system"
    # System prompt should contain chunk text from the KB
    assert "return" in system_msg["content"].lower()


@patch("backend.agents.knowledge_agent.litellm.acompletion", new_callable=AsyncMock)
@patch("backend.agents.knowledge_agent.litellm.aembedding", new_callable=AsyncMock)
async def test_chunks_retrieved_count_in_actions(mock_embed, mock_complete, db, kb_data):
    mock_embed.return_value = make_embed_response()
    mock_complete.return_value = make_completion_response()

    result = await knowledge_agent_node(make_state(), {"configurable": {"db": db}})

    chunks_retrieved = result["actions_taken"][0]["chunks_retrieved"]
    assert chunks_retrieved > 0


@patch("backend.agents.knowledge_agent.litellm.acompletion", new_callable=AsyncMock)
@patch("backend.agents.knowledge_agent.litellm.aembedding", new_callable=AsyncMock)
async def test_no_kb_chunks_returns_zero_confidence(mock_embed, mock_complete, db):
    # No KB data inserted — empty table
    mock_embed.return_value = make_embed_response()
    mock_complete.return_value = make_completion_response()

    result = await knowledge_agent_node(make_state(), {"configurable": {"db": db}})

    assert result["confidence"] == 0.0
