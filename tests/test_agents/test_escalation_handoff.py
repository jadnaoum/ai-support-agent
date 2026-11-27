import pytest
"""
Classifier "decided" trigger, account-holder context, and the contextual
handoff message on code-triggered escalations. All LLM calls are mocked.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents.conversation import _classify_intent, _do_escalate, conversation_agent_node
from backend.agents.escalation import ESCALATION_TRIGGER_REASONS, _HANDOFF_MESSAGE

MOD = "backend.agents.conversation"


def _completion(content: str) -> MagicMock:
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = content
    return mock


def _state(**overrides) -> dict:
    base = {
        "messages": [{"role": "customer", "content": "Hi, I'm Mareike, I'm not Triss"}],
        "customer_id": str(uuid.uuid4()),
        "customer_context": {"name": "Triss"},
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


async def test_decided_trigger_maps_to_classifier_decided():
    assert "classifier_decided" in ESCALATION_TRIGGER_REASONS
    with patch(f"{MOD}.check_input", new_callable=AsyncMock, return_value={"safe": True}), \
         patch(f"{MOD}._classify_intent", new_callable=AsyncMock,
               return_value=("escalation_request", 0.95, {"trigger": "decided"}, "")), \
         patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock,
               return_value=_completion("text")):
        result = await conversation_agent_node(_state(), {})
    assert result["requires_escalation"] is True
    assert result["escalation_reason"] == "classifier_decided"


async def test_classifier_sees_account_holder_after_cache_breakpoint():
    with patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock,
               return_value=_completion('{"intent": "general", "confidence": 0.9}')) as llm:
        await _classify_intent(_state(), {})
    blocks = llm.call_args.kwargs["messages"][0]["content"]
    assert "cache_control" in blocks[0]
    assert blocks[1]["text"].strip() == "Account holder: Triss"


async def test_classifier_omits_account_holder_when_unknown():
    with patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock,
               return_value=_completion('{"intent": "general", "confidence": 0.9}')) as llm:
        await _classify_intent(_state(customer_context={}), {})
    assert len(llm.call_args.kwargs["messages"][0]["content"]) == 1


async def test_reply_step_writes_context_aware_handoffs():
    """classifier_decided & co: the reply step writes the handoff with the
    handoff snippet + reason label, and the output guard checks it."""
    with patch(f"{MOD}._generate_response", new_callable=AsyncMock,
               return_value="Since this account belongs to someone else, I'm passing this to a colleague.") as gen, \
         patch(f"{MOD}.check_output", new_callable=AsyncMock, return_value={"safe": True}) as guard, \
         patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock, return_value=_completion("Internal briefing.")):
        result = await _do_escalate("classifier_decided", _state(), {})
    assert result["response"].startswith("Since this account belongs to someone else")
    assert gen.call_args.kwargs["handoff_reason"] == "classifier_decided"
    guard.assert_awaited_once()
    # the guard sees the handoff as an action taken, so "I'm passing this to a
    # colleague" isn't read as a claim about an action no tool performed
    guard_state = guard.call_args.args[1]
    assert {"service": "escalation_handler", "action": "escalate", "reason": "classifier_decided"} in guard_state["actions_taken"]
    assert result["context_summary"].startswith("Internal briefing.")


async def test_blocked_handoff_draft_falls_back_to_fixed_sentence():
    with patch(f"{MOD}._generate_response", new_callable=AsyncMock, return_value="Invented claim."), \
         patch(f"{MOD}.check_output", new_callable=AsyncMock, return_value={"safe": False, "reason": "unsupported_claim"}), \
         patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock, return_value=_completion("Internal briefing.")):
        result = await _do_escalate("customer_frustrated", _state(), {})
    assert result["response"] == _HANDOFF_MESSAGE


@pytest.mark.parametrize("reason", ["abusive_input", "provocative_input", "output_guard_block", "unhandled_error"])
async def test_off_the_rails_and_error_handoffs_use_the_fixed_sentence(reason):
    with patch(f"{MOD}._generate_response", new_callable=AsyncMock) as gen, \
         patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock, return_value=_completion("Internal briefing.")):
        result = await _do_escalate(reason, _state(), {})
    gen.assert_not_awaited()
    assert result["response"] == _HANDOFF_MESSAGE


def test_handoff_snippet_and_reason_reach_the_reply_context():
    from backend.agents.conversation import _build_context_section
    ctx = _build_context_section({"customer_context": {}}, handoff_reason="customer_frustrated")
    assert "handing this conversation to a colleague" in ctx
    assert "Handoff reason: customer_frustrated" in ctx


async def test_escalation_falls_back_to_fixed_message_on_error():
    with patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock,
               side_effect=RuntimeError("api down")):
        result = await _do_escalate("repeated_blocks", _state(), {})
    assert result["response"] == _HANDOFF_MESSAGE
    assert result["requires_escalation"] is True


# ---------------------------------------------------------------------------
# Pass-2 escalation priority: tool flag beats guard handoff detection
# ---------------------------------------------------------------------------

def _pass2_state(action_results):
    return _state(
        messages=[{"role": "customer", "content": "I think the mailman stole it"}],
        action_results=action_results,
        actions_taken=[{"service": "action_service", "action": "check_missing_package"}],
    )


async def test_tool_flag_wins_over_guard_handoff_detection():
    """UI test 2026-11-04 (conv 6278c5d4): carrier_claim_eligible was filed as
    llm_escalation_intent because handoff detection ran first."""
    with patch(f"{MOD}._generate_response", new_callable=AsyncMock,
               return_value="…file a police report… I'm handing you off to a specialist."), \
         patch(f"{MOD}.check_output", new_callable=AsyncMock,
               return_value={"safe": True, "handoff_intent": True}), \
         patch(f"{MOD}.run_escalation_side_effects", new_callable=AsyncMock) as side_effects, \
         patch(f"{MOD}.summarize_for_handoff", new_callable=AsyncMock, return_value="briefing"):
        result = await conversation_agent_node(_pass2_state(
            [{"success": True, "reason": "carrier_claim_eligible", "requires_escalation": True}]
        ), {})
    assert result["escalation_reason"] == "carrier_claim_eligible"
    assert result["response"].endswith("I'm handing you off to a specialist.")
    side_effects.assert_awaited_once()
    assert side_effects.call_args.args[0] == "carrier_claim_eligible"


async def test_guard_handoff_still_escalates_without_tool_flag():
    with patch(f"{MOD}._generate_response", new_callable=AsyncMock,
               return_value="Let me connect you with a specialist."), \
         patch(f"{MOD}.check_output", new_callable=AsyncMock,
               return_value={"safe": True, "handoff_intent": True}), \
         patch(f"{MOD}.run_escalation_side_effects", new_callable=AsyncMock) as side_effects, \
         patch(f"{MOD}.summarize_for_handoff", new_callable=AsyncMock, return_value="briefing"):
        result = await conversation_agent_node(_pass2_state([{"success": True, "reason": "tracking_info"}]), {})
    assert result["escalation_reason"] == "llm_escalation_intent"
    side_effects.assert_awaited_once()


async def test_no_signal_means_no_escalation():
    with patch(f"{MOD}._generate_response", new_callable=AsyncMock, return_value="It shipped yesterday."), \
         patch(f"{MOD}.check_output", new_callable=AsyncMock,
               return_value={"safe": True, "handoff_intent": False}), \
         patch(f"{MOD}.run_escalation_side_effects", new_callable=AsyncMock) as side_effects:
        result = await conversation_agent_node(_pass2_state([{"success": True, "reason": "tracking_info"}]), {})
    assert not result.get("requires_escalation")
    side_effects.assert_not_awaited()
