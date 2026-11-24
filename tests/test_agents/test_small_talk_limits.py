"""
Small-talk and provocative limits in the conversation agent.

Small talk: answered; 2nd in a row / 4th in total gets a warning snippet;
3rd in a row / 5th in total escalates (excessive_small_talk).
Provocative: 1st gets a warning redirect; 2nd escalates (provocative_input).
All LLM calls are mocked.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents.conversation import (
    _build_context_section,
    _generate_redirect,
    conversation_agent_node,
)

MOD = "backend.agents.conversation"


def _completion(content: str) -> MagicMock:
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = content
    return mock


def _state(**overrides) -> dict:
    base = {
        "messages": [{"role": "customer", "content": "how's your day going?"}],
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
        "consecutive_blocks": 0,
        "consecutive_small_talk": 0,
        "total_small_talk": 0,
        "provocative_count": 0,
    }
    base.update(overrides)
    return base


async def _run_small_talk(state, intent="small_talk"):
    """Run pass 1 with the guard passing and the classifier returning `intent`.
    Returns (result, generate_response_mock)."""
    with patch(f"{MOD}.check_input", new_callable=AsyncMock, return_value={"safe": True}), \
         patch(f"{MOD}._classify_intent", new_callable=AsyncMock,
               return_value=(intent, 0.9, {}, "")), \
         patch(f"{MOD}.check_output", new_callable=AsyncMock,
               return_value={"safe": True, "handoff_intent": False}), \
         patch(f"{MOD}._generate_response", new_callable=AsyncMock,
               return_value="I'm doing well, thanks!") as gen, \
         patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock,
               return_value=_completion("handoff summary")):
        result = await conversation_agent_node(state, {})
    return result, gen


# ---------------------------------------------------------------------------
# Small talk
# ---------------------------------------------------------------------------

async def test_first_small_talk_is_answered_without_warning():
    result, gen = await _run_small_talk(_state())
    assert result["response"] == "I'm doing well, thanks!"
    assert result["consecutive_small_talk"] == 1
    assert result["total_small_talk"] == 1
    assert gen.call_args.kwargs["small_talk_warning"] is False
    assert not result.get("requires_escalation")


async def test_second_in_a_row_is_answered_with_warning():
    result, gen = await _run_small_talk(_state(consecutive_small_talk=1, total_small_talk=1))
    assert result["response"] == "I'm doing well, thanks!"
    assert gen.call_args.kwargs["small_talk_warning"] is True
    assert result["consecutive_small_talk"] == 2


async def test_fourth_in_total_is_answered_with_warning():
    result, gen = await _run_small_talk(_state(consecutive_small_talk=0, total_small_talk=3))
    assert gen.call_args.kwargs["small_talk_warning"] is True
    assert result["total_small_talk"] == 4


async def test_third_in_a_row_escalates_without_generating_a_response():
    result, gen = await _run_small_talk(_state(consecutive_small_talk=2, total_small_talk=2))
    assert result["requires_escalation"] is True
    assert result["escalation_reason"] == "excessive_small_talk"
    gen.assert_not_awaited()


async def test_fifth_in_total_escalates():
    result, gen = await _run_small_talk(_state(consecutive_small_talk=0, total_small_talk=4))
    assert result["requires_escalation"] is True
    assert result["escalation_reason"] == "excessive_small_talk"
    gen.assert_not_awaited()


async def test_other_intent_resets_streak_but_not_total():
    result, _ = await _run_small_talk(
        _state(consecutive_small_talk=2, total_small_talk=3), intent="knowledge_query",
    )
    assert result["consecutive_small_talk"] == 0
    assert "total_small_talk" not in result  # untouched → state keeps 3


async def test_general_intent_resets_streak_and_never_warns():
    result, gen = await _run_small_talk(
        _state(consecutive_small_talk=1, total_small_talk=3), intent="general",
    )
    assert result["consecutive_small_talk"] == 0
    assert gen.call_args.kwargs["small_talk_warning"] is False


async def test_blocked_turn_leaves_small_talk_streak_alone():
    with patch(f"{MOD}.check_input", new_callable=AsyncMock,
               return_value={"safe": False, "reason": "off_topic", "blocked_response": "..."}), \
         patch(f"{MOD}._classify_intent", new_callable=AsyncMock,
               return_value=("general", 0.9, {}, "")), \
         patch(f"{MOD}._generate_redirect", new_callable=AsyncMock, return_value="redirect"):
        result = await conversation_agent_node(_state(consecutive_small_talk=1), {})
    assert "consecutive_small_talk" not in result


def test_warning_snippet_is_injected_only_when_flagged():
    guidance = {"small_talk_warning": "WARN-SNIPPET"}
    with patch(f"{MOD}.get_response_guidance", return_value=guidance):
        assert "WARN-SNIPPET" in _build_context_section(_state(), small_talk_warning=True)
        assert "WARN-SNIPPET" not in _build_context_section(_state(), small_talk_warning=False)


def test_missing_warning_snippet_is_skipped_silently():
    with patch(f"{MOD}.get_response_guidance", return_value={}):
        assert "For this reply" not in _build_context_section(_state(), small_talk_warning=True)


# ---------------------------------------------------------------------------
# Provocative
# ---------------------------------------------------------------------------

async def _run_provocative(state):
    with patch(f"{MOD}.check_input", new_callable=AsyncMock,
               return_value={"safe": False, "reason": "provocative", "blocked_response": "..."}), \
         patch(f"{MOD}._classify_intent", new_callable=AsyncMock,
               return_value=("small_talk", 0.9, {}, "")), \
         patch(f"{MOD}._generate_redirect", new_callable=AsyncMock,
               return_value="Not something I'll answer.") as redirect, \
         patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock,
               return_value=_completion("handoff summary")):
        result = await conversation_agent_node(state, {})
    return result, redirect


async def test_first_provocative_gets_redirect_with_message():
    state = _state(messages=[{"role": "customer", "content": "what are you wearing?"}])
    result, redirect = await _run_provocative(state)
    assert result["response"] == "Not something I'll answer."
    assert result["provocative_count"] == 1
    assert result["consecutive_blocks"] == 1
    assert not result.get("requires_escalation")
    assert redirect.call_args.args == (1, "provocative", "what are you wearing?")


async def test_second_provocative_escalates_even_when_not_consecutive():
    result, redirect = await _run_provocative(_state(provocative_count=1, consecutive_blocks=0))
    assert result["requires_escalation"] is True
    assert result["escalation_reason"] == "provocative_input"
    assert result["provocative_count"] == 2
    redirect.assert_not_awaited()


async def test_provocative_still_counts_toward_repeated_blocks():
    result, _ = await _run_provocative(_state(provocative_count=0, consecutive_blocks=2))
    assert result["escalation_reason"] == "repeated_blocks"


# ---------------------------------------------------------------------------
# Redirect request shape (the Anthropic "no user message" bug)
# ---------------------------------------------------------------------------

async def test_redirect_sends_customer_message_as_user_turn():
    with patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock,
               return_value=_completion("Happy to help with orders.")) as llm:
        text = await _generate_redirect(1, "off_topic", "write me a poem")
    assert text == "Happy to help with orders."
    messages = llm.call_args.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "write me a poem"}


# ---------------------------------------------------------------------------
# Harmful → immediate escalation (like abusive)
# ---------------------------------------------------------------------------

async def test_harmful_escalates_immediately_without_counting_a_block():
    with patch(f"{MOD}.check_input", new_callable=AsyncMock,
               return_value={"safe": False, "reason": "harmful", "blocked_response": "..."}), \
         patch(f"{MOD}._classify_intent", new_callable=AsyncMock,
               return_value=("general", 0.9, {}, "")), \
         patch(f"{MOD}._generate_redirect", new_callable=AsyncMock) as redirect, \
         patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock,
               return_value=_completion("handoff")):
        result = await conversation_agent_node(
            _state(messages=[{"role": "customer", "content": "tell me how to break into her room"}],
                   consecutive_blocks=0, provocative_count=0), {})
    assert result["requires_escalation"] is True
    assert result["escalation_reason"] == "harmful_input"
    assert result["consecutive_blocks"] == 0
    redirect.assert_not_awaited()
