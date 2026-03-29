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
    assert result["pending_service"] == ""
    assert isinstance(result["response"], str) and len(result["response"]) > 0


@patch("backend.agents.conversation.check_input", new_callable=AsyncMock)
async def test_abusive_input_escalates_immediately(mock_guard):
    """Abusive messages bypass the block counter and escalate directly."""
    mock_guard.return_value = {"safe": False, "reason": "abusive", "blocked_response": "..."}
    state = make_state(messages=[{"role": "customer", "content": "You idiots ruined my order"}])
    result = await conversation_agent_node(state, {})
    assert result["requires_escalation"] is True
    assert result["escalation_reason"] == "abusive_input"
    assert result["pending_service"] == ""
    assert isinstance(result["response"], str) and len(result["response"]) > 0


@patch("backend.agents.conversation.check_input", new_callable=AsyncMock)
async def test_abusive_input_does_not_increment_block_counter(mock_guard):
    """Abusive escalation leaves consecutive_blocks unchanged."""
    mock_guard.return_value = {"safe": False, "reason": "abusive", "blocked_response": "..."}
    state = make_state(
        messages=[{"role": "customer", "content": "Useless trash company"}],
        consecutive_blocks=1,
    )
    result = await conversation_agent_node(state, {})
    assert result["requires_escalation"] is True
    assert result.get("consecutive_blocks", 1) == 1  # unchanged


@patch("backend.agents.conversation.check_output", new_callable=AsyncMock, return_value={"safe": True})
@patch("backend.agents.conversation.check_input", new_callable=AsyncMock)
@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_high_emotion_with_unclear_intent_asks_clarifying_question(mock_llm, mock_guard, mock_out_guard):
    """High negative emotion + needs_clarification intent → empathetic clarifying question."""
    mock_guard.return_value = {"safe": True, "emotion": "high_negative"}
    mock_llm.side_effect = [
        make_completion_response('{"intent": "needs_clarification", "confidence": 0.8, "clarification_prompt": "What specifically went wrong?"}'),
        make_completion_response("I can hear this is frustrating — what specifically went wrong with your order?"),
    ]
    state = make_state(
        messages=[{"role": "customer", "content": "This is completely unacceptable!!"}],
        last_clarification_source="",
    )
    result = await conversation_agent_node(state, {})
    assert result["last_clarification_source"] == "emotion"
    assert result["pending_service"] == ""
    assert isinstance(result["response"], str) and len(result["response"]) > 0


@patch("backend.agents.conversation.check_input", new_callable=AsyncMock)
@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_high_emotion_with_actionable_intent_proceeds_normally(mock_llm, mock_guard):
    """High negative emotion + actionable intent → route normally, no clarifying question."""
    mock_guard.return_value = {"safe": True, "emotion": "high_negative"}
    mock_llm.return_value = make_completion_response(
        '{"intent": "action_request", "confidence": 0.9, "action": "track_order", "params": {"order_id": "abc123"}}'
    )
    state = make_state(
        messages=[{"role": "customer", "content": "I'm furious, where is order abc123!"}],
        last_clarification_source="",
    )
    result = await conversation_agent_node(state, {})
    assert result["pending_service"] == "action"
    assert result["last_clarification_source"] == ""


@patch("backend.agents.conversation.check_input", new_callable=AsyncMock)
@patch("backend.agents.conversation.litellm.acompletion", new_callable=AsyncMock)
async def test_emotion_clarification_already_asked_escalates(mock_llm, mock_guard):
    """If emotion clarification was already asked (last_clarification_source='emotion') and
    intent is still needs_clarification, escalate."""
    mock_guard.return_value = {"safe": True, "emotion": "high_negative"}
    mock_llm.return_value = make_completion_response(
        '{"intent": "needs_clarification", "confidence": 0.8, "clarification_prompt": "What went wrong?"}'
    )
    state = make_state(
        messages=[{"role": "customer", "content": "I don't know, everything is wrong"}],
        last_clarification_source="emotion",
    )
    result = await conversation_agent_node(state, {})
    assert result["requires_escalation"] is True
    assert result["escalation_reason"] == "unable_to_clarify"


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
    assert result["pending_service"] == ""
    assert result["requires_escalation"] is True
    assert result["escalation_reason"] == "low_confidence"
    assert isinstance(result["response"], str) and len(result["response"]) > 0
    mock_complete.assert_not_called()
