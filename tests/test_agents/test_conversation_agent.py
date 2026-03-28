"""
Tests for the conversation agent node.
All LiteLLM calls are mocked — no real API calls.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents.conversation import conversation_agent_node


def make_completion_response(content: str) -> MagicMock:
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = content
    return mock


def make_state(**overrides) -> dict:
    base = {
        "messages": [{"role": "customer", "content": "What is the return policy?"}],
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
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Pass 1: intent classification
# ---------------------------------------------------------------------------

@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_knowledge_query_sets_pending_service(mock_complete):
    mock_complete.return_value = make_completion_response(
        '{"intent": "knowledge_query", "confidence": 0.9}'
    )
    result = await conversation_agent_node(make_state(), {})
    assert result["pending_service"] == "knowledge"
    assert "response" not in result or result.get("response") == ""


@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_knowledge_query_sets_confidence(mock_complete):
    mock_complete.return_value = make_completion_response(
        '{"intent": "knowledge_query", "confidence": 0.92}'
    )
    result = await conversation_agent_node(make_state(), {})
    assert abs(result["confidence"] - 0.92) < 0.01


@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_action_request_sets_pending_service_and_action(mock_complete):
    mock_complete.return_value = make_completion_response(
        '{"intent": "action_request", "confidence": 0.95, "action": "cancel_order", "params": {"order_id": "abc123"}}'
    )
    state = make_state(messages=[{"role": "customer", "content": "Cancel my order abc123"}])
    result = await conversation_agent_node(state, {})
    assert result["pending_service"] == "action"
    assert result["pending_action"]["tool"] == "cancel_order"
    assert result["pending_action"]["params"]["order_id"] == "abc123"


@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_escalation_request_sets_requires_escalation(mock_complete):
    mock_complete.return_value = make_completion_response(
        '{"intent": "escalation_request", "confidence": 0.95}'
    )
    state = make_state(messages=[{"role": "customer", "content": "I want to speak to a human"}])
    result = await conversation_agent_node(state, {})
    assert result["requires_escalation"] is True
    assert result["escalation_reason"] == "customer_requested"
    assert result["pending_service"] == "escalation"


@patch("backend.agents.conversation.check_output", new_callable=AsyncMock, return_value={"safe": True})
@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_general_intent_responds_directly(mock_complete, mock_guard):
    async def dispatch(*args, **kwargs):
        messages = kwargs.get("messages", [])
        system = messages[0]["content"] if messages else ""
        if "intent classifier" in system.lower():
            return make_completion_response('{"intent": "general", "confidence": 0.99}')
        return make_completion_response("Hello! How can I help you today?")

    mock_complete.side_effect = dispatch
    state = make_state(messages=[{"role": "customer", "content": "Hello!"}])
    result = await conversation_agent_node(state, {})
    assert result["response"] == "Hello! How can I help you today?"
    assert result["pending_service"] == ""


@patch("backend.agents.conversation.check_output", new_callable=AsyncMock, return_value={"safe": True})
@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_malformed_intent_json_falls_back_to_general(mock_complete, mock_guard):
    async def dispatch(*args, **kwargs):
        messages = kwargs.get("messages", [])
        system = messages[0]["content"] if messages else ""
        if "intent classifier" in system.lower():
            return make_completion_response("not valid json")
        return make_completion_response("Sure, I can help with that.")

    mock_complete.side_effect = dispatch
    result = await conversation_agent_node(make_state(), {})
    # Falls back to general → responds directly (no pending_service)
    assert result["pending_service"] == ""
    assert result["response"] == "Sure, I can help with that."


# ---------------------------------------------------------------------------
# Pass 2: response generation with service results
# ---------------------------------------------------------------------------

FAKE_KB_ACTION = [{"service": "knowledge_service", "action": "search_kb", "chunks_retrieved": 1}]


@patch("backend.agents.conversation.check_output", new_callable=AsyncMock, return_value={"safe": True})
@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_generates_response_with_kb_context(mock_complete, mock_guard):
    mock_complete.return_value = make_completion_response(
        "Your return window is 30 days for most items."
    )
    state = make_state(
        retrieved_context=[
            {
                "chunk_text": "Standard return window is 30 days.",
                "title": "Returns Policy",
                "category": "returns",
                "similarity": 0.91,
            }
        ],
        actions_taken=FAKE_KB_ACTION,  # signals pass 2
    )
    result = await conversation_agent_node(state, {})
    assert result["response"] == "Your return window is 30 days for most items."
    assert result["pending_service"] == ""


@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_response_pass_uses_top_chunk_similarity_as_confidence(mock_complete):
    mock_complete.return_value = make_completion_response("Here is your answer.")
    state = make_state(
        retrieved_context=[
            {"chunk_text": "...", "title": "T", "category": "c", "similarity": 0.87}
        ],
        actions_taken=FAKE_KB_ACTION,
    )
    result = await conversation_agent_node(state, {})
    assert abs(result["confidence"] - 0.87) < 0.01


@patch("backend.agents.conversation.check_output", new_callable=AsyncMock, return_value={"safe": True})
@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_system_prompt_includes_kb_context(mock_complete, mock_guard):
    mock_complete.return_value = make_completion_response("Answer.")
    state = make_state(
        retrieved_context=[
            {
                "chunk_text": "Electronics have a 14-day return window.",
                "title": "Returns Policy",
                "category": "returns",
                "similarity": 0.9,
            }
        ],
        actions_taken=FAKE_KB_ACTION,
    )
    await conversation_agent_node(state, {})
    messages = mock_complete.call_args.kwargs["messages"]
    system_msg = messages[0]
    assert system_msg["role"] == "system"
    assert "Electronics" in system_msg["content"]


@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_low_confidence_kb_result_triggers_escalation(mock_complete):
    """When KB similarity is below the confidence threshold, escalate instead of responding."""
    # No LLM call should be made — escalation decision is based on similarity score alone
    state = make_state(
        retrieved_context=[
            {"chunk_text": "...", "title": "T", "category": "c", "similarity": 0.1}
        ],
        actions_taken=FAKE_KB_ACTION,
    )
    result = await conversation_agent_node(state, {})
    assert result["pending_service"] == "escalation"
    assert result["requires_escalation"] is True
    assert result["escalation_reason"] == "low_confidence"
    mock_complete.assert_not_called()
