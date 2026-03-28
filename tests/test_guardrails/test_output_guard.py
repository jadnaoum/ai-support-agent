"""
Tests for the LLM-based output guardrail.

check_output() is now async and delegates to litellm — tests mock the LLM
call and verify that verdict routing, context assembly, and fallback
behaviour are all correct.
"""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def make_state(**overrides) -> dict:
    base = {
        "messages": [{"role": "customer", "content": "Help me please."}],
        "customer_id": str(uuid.uuid4()),
        "customer_context": {},
        "retrieved_context": [],
        "action_results": [],
        "confidence": 0.9,
        "requires_escalation": False,
        "escalation_reason": "",
        "actions_taken": [],
        "response": "",
        "pending_service": "",
        "pending_action": {},
    }
    base.update(overrides)
    return base


def make_llm_response(verdict: str, failure_type: str = None, reason: str = None):
    """Build a mock litellm response with the given guard verdict."""
    payload = {"verdict": verdict, "failure_type": failure_type, "reason": reason}
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = json.dumps(payload)
    return mock


# ---------------------------------------------------------------------------
# Pass path
# ---------------------------------------------------------------------------

@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_clean_response_passes(mock_complete):
    from backend.guardrails.output_guard import check_output
    mock_complete.return_value = make_llm_response("pass")
    result = await check_output("I'd be happy to help with your return.", make_state())
    assert result["safe"] is True


@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_future_tense_promise_passes(mock_complete):
    """'I'll cancel your order' is a forward promise — should pass."""
    mock_complete.return_value = make_llm_response("pass")
    from backend.guardrails.output_guard import check_output
    result = await check_output("I'll cancel your order for you right now.", make_state())
    assert result["safe"] is True


@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_response_with_known_id_passes(mock_complete):
    mock_complete.return_value = make_llm_response("pass")
    known_id = str(uuid.uuid4())
    from backend.guardrails.output_guard import check_output
    state = make_state(
        messages=[{"role": "customer", "content": f"Track my order {known_id}"}]
    )
    result = await check_output(f"Order {known_id} is on its way!", state)
    assert result["safe"] is True


# ---------------------------------------------------------------------------
# Fail path — impossible promise
# ---------------------------------------------------------------------------

@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_impossible_promise_is_blocked(mock_complete):
    mock_complete.return_value = make_llm_response("fail", "impossible_promise", "Agent claimed cancellation without calling cancel_order.")
    from backend.guardrails.output_guard import check_output
    result = await check_output("I've cancelled your order successfully.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "impossible_promise"


@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_refund_claim_without_tool_is_blocked(mock_complete):
    mock_complete.return_value = make_llm_response("fail", "impossible_promise", "process_refund was not called.")
    from backend.guardrails.output_guard import check_output
    result = await check_output("Your refund has been processed.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "impossible_promise"


@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_claim_with_matching_tool_passes(mock_complete):
    mock_complete.return_value = make_llm_response("pass")
    from backend.guardrails.output_guard import check_output
    state = make_state(actions_taken=[{"service": "action_service", "action": "cancel_order"}])
    result = await check_output("I've cancelled your order successfully.", state)
    assert result["safe"] is True


# ---------------------------------------------------------------------------
# Fail path — hallucinated ID
# ---------------------------------------------------------------------------

@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_fabricated_id_is_blocked(mock_complete):
    mock_complete.return_value = make_llm_response("fail", "hallucinated_id", "UUID in response was not in known IDs.")
    from backend.guardrails.output_guard import check_output
    fabricated = str(uuid.uuid4())
    result = await check_output(f"Your order {fabricated} has been placed.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "hallucinated_id"


# ---------------------------------------------------------------------------
# Fail path — new categories the LLM guard can now catch
# ---------------------------------------------------------------------------

@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_system_disclosure_is_blocked(mock_complete):
    mock_complete.return_value = make_llm_response("fail", "system_disclosure", "Response revealed internal tool names.")
    from backend.guardrails.output_guard import check_output
    result = await check_output("I use the cancel_order tool to process cancellations.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "system_disclosure"


@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_hallucinated_policy_is_blocked(mock_complete):
    mock_complete.return_value = make_llm_response("fail", "hallucinated_policy", "Return window of 90 days not in KB.")
    from backend.guardrails.output_guard import check_output
    result = await check_output("You have 90 days to return your item.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "hallucinated_policy"


@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_speculative_claim_is_blocked(mock_complete):
    mock_complete.return_value = make_llm_response("fail", "speculative_claim", "Agent guaranteed delivery date.")
    from backend.guardrails.output_guard import check_output
    result = await check_output("It will definitely arrive by Thursday.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "speculative_claim"


# ---------------------------------------------------------------------------
# Error / fallback behaviour — fail closed
# ---------------------------------------------------------------------------

@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_llm_error_fails_closed(mock_complete):
    """On any LLM exception the guard must block the response, not let it through."""
    mock_complete.side_effect = Exception("LLM timeout")
    from backend.guardrails.output_guard import check_output
    result = await check_output("Here is your order status.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "guard_error"


@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_malformed_json_fails_closed(mock_complete):
    """Malformed LLM JSON → fail closed."""
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = "not valid json at all"
    mock_complete.return_value = mock
    from backend.guardrails.output_guard import check_output
    result = await check_output("Here is your order status.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "guard_error"


# ---------------------------------------------------------------------------
# Context is passed to the LLM (verify the prompt gets built and sent)
# ---------------------------------------------------------------------------

@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_llm_is_called_with_response_text(mock_complete):
    """The LLM must be called; response text must appear in the prompt."""
    mock_complete.return_value = make_llm_response("pass")
    from backend.guardrails.output_guard import check_output
    response_text = "Your order is on its way and should arrive soon."
    await check_output(response_text, make_state())
    mock_complete.assert_called_once()
    call_kwargs = mock_complete.call_args
    prompt = call_kwargs[1]["messages"][0]["content"]
    assert response_text in prompt


@patch("backend.guardrails.output_guard.litellm.acompletion", new_callable=AsyncMock)
async def test_known_id_included_in_prompt(mock_complete):
    """IDs from customer context must appear in the prompt so the LLM can verify them."""
    mock_complete.return_value = make_llm_response("pass")
    from backend.guardrails.output_guard import check_output
    known_id = str(uuid.uuid4())
    state = make_state(
        customer_context={
            "recent_orders": [{"order_id": known_id, "status": "shipped", "total": 50.0, "placed_at": None}]
        }
    )
    await check_output("Your order is shipped.", state)
    prompt = mock_complete.call_args[1]["messages"][0]["content"]
    assert known_id in prompt
