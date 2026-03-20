"""
Tests for the LangGraph graph structure and conversation agent routing.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from langgraph.graph import END

from backend.agents.graph import AgentState, build_graph, _route_after_conversation


def make_state(**overrides) -> AgentState:
    base: AgentState = {
        "messages": [{"role": "customer", "content": "Hello"}],
        "customer_id": str(uuid.uuid4()),
        "customer_context": {},
        "retrieved_context": [],
        "action_results": [],
        "confidence": 0.0,
        "requires_escalation": False,
        "escalation_reason": "",
        "actions_taken": [],
        "response": "",
        "pending_service": "",
        "pending_action": {},
    }
    base.update(overrides)
    return base


def make_completion_response(content: str) -> MagicMock:
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = content
    return mock


# ---------------------------------------------------------------------------
# Routing logic
# ---------------------------------------------------------------------------

def test_route_returns_knowledge_service_when_pending():
    state = make_state(pending_service="knowledge")
    assert _route_after_conversation(state) == "knowledge_service"


def test_route_returns_action_service_when_pending():
    state = make_state(pending_service="action")
    assert _route_after_conversation(state) == "action_service"


def test_route_returns_end_when_no_pending():
    state = make_state(pending_service="")
    assert _route_after_conversation(state) == END


def test_route_returns_end_when_pending_service_missing():
    state = make_state()
    del state["pending_service"]
    assert _route_after_conversation(state) == END


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------

def test_graph_builds_without_error():
    g = build_graph()
    assert g is not None


def test_graph_has_expected_nodes():
    g = build_graph()
    assert "conversation_agent" in g.nodes
    assert "knowledge_service" in g.nodes
    assert "action_service" in g.nodes


# ---------------------------------------------------------------------------
# Full graph invoke (mocked LLM + embedding)
# ---------------------------------------------------------------------------

@patch("backend.agents.knowledge_service.litellm.aembedding", new_callable=AsyncMock)
@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_graph_invoke_knowledge_query_returns_response(mock_complete, mock_embed, db):
    mock_embed.return_value = {"data": [{"embedding": [0.1] * 1536}]}

    # Dispatch by system prompt content to avoid side_effect list exhaustion on Python 3.9
    async def dispatch(*args, **kwargs):
        messages = kwargs.get("messages", [])
        system = messages[0]["content"] if messages else ""
        if "intent classifier" in system.lower():
            return make_completion_response('{"intent": "knowledge_query", "confidence": 0.9}')
        return make_completion_response("Here is your answer.")

    mock_complete.side_effect = dispatch

    g = build_graph()
    state = make_state(messages=[{"role": "customer", "content": "What is the return policy?"}])
    result = await g.ainvoke(state, config={"configurable": {"db": db}})

    assert result["response"] == "Here is your answer."
    assert len(result["actions_taken"]) == 1
    assert result["actions_taken"][0]["service"] == "knowledge_service"


@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_graph_invoke_general_query_skips_knowledge_service(mock_complete, db):
    async def dispatch(*args, **kwargs):
        messages = kwargs.get("messages", [])
        system = messages[0]["content"] if messages else ""
        if "intent classifier" in system.lower():
            return make_completion_response('{"intent": "general", "confidence": 0.99}')
        return make_completion_response("Hello! How can I help?")

    mock_complete.side_effect = dispatch

    g = build_graph()
    state = make_state(messages=[{"role": "customer", "content": "Hi there"}])
    result = await g.ainvoke(state, config={"configurable": {"db": db}})

    assert result["response"] == "Hello! How can I help?"
    assert result["actions_taken"] == []
