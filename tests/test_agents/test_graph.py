"""
Tests for the LangGraph graph structure and supervisor routing.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.graph import AgentState, build_graph
from backend.agents.supervisor import supervisor_node, route_after_supervisor


def make_state(**overrides) -> AgentState:
    base: AgentState = {
        "messages": [{"role": "customer", "content": "Hello"}],
        "customer_id": str(uuid.uuid4()),
        "customer_context": {},
        "current_intent": "",
        "routing_decision": "",
        "confidence": 0.0,
        "response": "",
        "requires_escalation": False,
        "actions_taken": [],
    }
    base.update(overrides)
    return base


async def test_supervisor_node_sets_intent():
    result = await supervisor_node(make_state(), {})
    assert result["current_intent"] == "knowledge_query"


async def test_supervisor_node_sets_routing_decision():
    result = await supervisor_node(make_state(), {})
    assert result["routing_decision"] == "hardcoded_knowledge_phase2"


def test_route_after_supervisor_returns_knowledge_agent():
    state = make_state(current_intent="knowledge_query")
    assert route_after_supervisor(state) == "knowledge_agent"


def test_route_after_supervisor_defaults_to_knowledge_agent():
    # Unknown intents default to knowledge_agent in Phase 2
    state = make_state(current_intent="unknown_intent")
    assert route_after_supervisor(state) == "knowledge_agent"


def test_graph_builds_without_error():
    g = build_graph()
    assert g is not None


def test_graph_has_expected_nodes():
    g = build_graph()
    assert "supervisor" in g.nodes
    assert "knowledge_agent" in g.nodes


@patch("backend.agents.knowledge_agent.litellm.acompletion", new_callable=AsyncMock)
@patch("backend.agents.knowledge_agent.litellm.aembedding", new_callable=AsyncMock)
async def test_graph_invoke_returns_response(mock_embed, mock_complete, db):
    from unittest.mock import MagicMock
    mock_embed.return_value = {"data": [{"embedding": [0.1] * 1536}]}
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Here is your answer."
    mock_complete.return_value = mock_response

    g = build_graph()
    state = make_state(messages=[{"role": "customer", "content": "What is the return policy?"}])
    result = await g.ainvoke(state, config={"configurable": {"db": db}})

    assert result["response"] == "Here is your answer."
    assert result["current_intent"] == "knowledge_query"
    assert result["routing_decision"] == "hardcoded_knowledge_phase2"
    assert len(result["actions_taken"]) == 1
