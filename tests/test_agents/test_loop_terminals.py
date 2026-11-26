"""
Routing-loop terminals (eval_gaps_v1 review): results where only the customer
can supply what's missing, or where the question is settled, end the loop; an
invented tool name from the routing step is never sent to action_service.
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.conversation import _service_needs_loop, conversation_agent_node
from backend.agents.mock_tools import mock_tool_call
from tests.test_agents.test_small_talk_limits import MOD, _state


def _after(result: dict) -> dict:
    return {"actions_taken": [{"service": "action_service", "action": "x"}], "action_results": [result]}


@pytest.mark.parametrize("result", [
    {"success": False, "reason": "reason_required"},                      # AE-002 / AE-033
    {"success": True, "reason": "no_eligible_orders", "eligible_orders": []},             # AE-040
])
def test_results_that_end_the_loop(result):
    assert _service_needs_loop(_after(result)) is False


def test_results_that_still_loop():
    # whether to go on after "eligible" is the routing step's call
    assert _service_needs_loop(_after({"success": True, "eligible": True, "reason": "eligible",
                                       "available_action": None})) is True
    assert _service_needs_loop(_after({"success": False, "reason": "already_delivered",
                                       "available_action": "check_return_eligibility"})) is True
    assert _service_needs_loop(_after({"success": False, "reason": "order_not_found"})) is True


async def test_unknown_tool_from_routing_step_is_not_called():
    state = _state(**_after({"success": False, "reason": "already_delivered",
                             "available_action": "check_return_eligibility"}))
    with patch(f"{MOD}._classify_next_step", new_callable=AsyncMock,
               return_value={"next": "action", "action": "initiate_refund", "params": {}}), \
         patch(f"{MOD}._generate_response", new_callable=AsyncMock, return_value="Here's where it stands."), \
         patch(f"{MOD}.check_output", new_callable=AsyncMock,
               return_value={"safe": True, "handoff_intent": False}):
        update = await conversation_agent_node(state, {})
    assert update.get("pending_service", "") == ""
    assert update["response"] == "Here's where it stands."


def test_mock_no_cancellable_orders_and_not_found():
    mock = {"orders": [{"id": "a", "status": "delivered"}, {"id": "b", "status": "shipped"}]}
    assert mock_tool_call("check_cancel_eligibility", {}, mock, "c", [])["reason"] == "no_eligible_orders"
    nf = mock_tool_call("track_order", {"order_id": "zzz"}, mock, "c", [])
    assert nf["reason"] == "order_not_found" and "error" not in nf
