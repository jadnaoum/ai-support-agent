"""
Frustration escalation (PENDING #37): the input guard's emotion flag.
extreme_negative hands off at once; high_negative hands off on the 2nd
frustrated turn in a row; a calm turn resets the streak; an explicit
escalation_request keeps its own reason; the classifier's own
"decided" escalation yields to customer_frustrated. All LLM calls are mocked.
"""
from unittest.mock import AsyncMock, patch

from backend.agents.conversation import conversation_agent_node
from tests.test_agents.test_small_talk_limits import MOD, _completion, _state


async def _turn(state, emotion="", intent="general", details=None):
    guard = {"safe": True, **({"emotion": emotion} if emotion else {})}
    with patch(f"{MOD}.check_input", new_callable=AsyncMock, return_value=guard), \
         patch(f"{MOD}._classify_intent", new_callable=AsyncMock,
               return_value=(intent, 0.9, details or {}, "")), \
         patch(f"{MOD}.check_output", new_callable=AsyncMock,
               return_value={"safe": True, "handoff_intent": False}), \
         patch(f"{MOD}._generate_response", new_callable=AsyncMock, return_value="On it."), \
         patch(f"{MOD}.litellm.acompletion", new_callable=AsyncMock,
               return_value=_completion("handoff summary")):
        return await conversation_agent_node(state, {})


async def test_first_frustrated_turn_is_answered_and_counted():
    result = await _turn(_state(), emotion="high_negative")
    assert not result.get("requires_escalation")
    assert result["consecutive_high_negative"] == 1


async def test_second_frustrated_turn_in_a_row_hands_off():
    result = await _turn(_state(consecutive_high_negative=1), emotion="high_negative", intent="action_request")
    assert result["requires_escalation"] and result["escalation_reason"] == "customer_frustrated"
    assert result["consecutive_high_negative"] == 2


async def test_extreme_distress_hands_off_at_once():
    result = await _turn(_state(), emotion="extreme_negative", intent="action_request")
    assert result["requires_escalation"] and result["escalation_reason"] == "customer_frustrated"


async def test_calm_turn_resets_the_streak():
    result = await _turn(_state(consecutive_high_negative=1))
    assert result["consecutive_high_negative"] == 0 and not result.get("requires_escalation")


async def test_explicit_escalation_keeps_its_own_reason():
    result = await _turn(_state(consecutive_high_negative=1), emotion="high_negative",
                         intent="escalation_request", details={"trigger": "threat"})
    assert result["escalation_reason"] == "threat"


async def test_frustration_overrides_classifier_decided():
    result = await _turn(_state(), emotion="extreme_negative",
                         intent="escalation_request", details={"trigger": "decided"})
    assert result["escalation_reason"] == "customer_frustrated"


async def test_classifier_decided_without_frustration_keeps_its_reason():
    result = await _turn(_state(), intent="escalation_request", details={"trigger": "decided"})
    assert result["escalation_reason"] == "classifier_decided"


async def test_blocked_turn_leaves_the_streak_alone():
    with patch(f"{MOD}.check_input", new_callable=AsyncMock,
               return_value={"safe": False, "reason": "off_topic", "blocked_response": "..."}), \
         patch(f"{MOD}._classify_intent", new_callable=AsyncMock, return_value=("general", 0.9, {}, "")), \
         patch(f"{MOD}._generate_redirect", new_callable=AsyncMock, return_value="redirect"):
        result = await conversation_agent_node(_state(consecutive_high_negative=1), {})
    assert "consecutive_high_negative" not in result
