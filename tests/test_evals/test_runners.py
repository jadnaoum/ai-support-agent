"""
Unit tests for eval runner functions.
Patches _call_agent_full and judge functions to test runner plumbing
without hitting the server or LLM.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


AGENT_STUB = {"response": "ok", "requires_escalation": False, "actions_taken": []}
JUDGMENT_STUB = {"verdict": "pass", "score": 1.0, "reasoning": "stub"}


@pytest.mark.asyncio
async def test_run_graceful_failure_passes_mock_account_state():
    """mock_account_state from the test case must reach _call_agent_full as mock_context."""
    test_case = {
        "conversation": "My order is missing.",
        "mock_account_state": '{"mock_handoff_intent": true}',
        "mock_agent_state": None,
    }

    captured = {}

    def fake_call_agent(messages, mock_context, **kwargs):
        captured["mock_context"] = mock_context
        return AGENT_STUB

    with patch("evals.run_evals._call_agent_full", side_effect=fake_call_agent), \
         patch("evals.run_evals.judge_graceful_failure", new=AsyncMock(return_value=JUDGMENT_STUB)):
        from evals.run_evals import run_graceful_failure
        await run_graceful_failure(test_case, calibrate=False)

    assert captured["mock_context"] == {"mock_handoff_intent": True}


@pytest.mark.asyncio
async def test_run_graceful_failure_empty_mock_account_state_passes_empty_dict():
    """Absent mock_account_state must result in {} (not None) reaching _call_agent_full."""
    test_case = {
        "conversation": "My order is missing.",
        "mock_account_state": None,
        "mock_agent_state": None,
    }

    captured = {}

    def fake_call_agent(messages, mock_context, **kwargs):
        captured["mock_context"] = mock_context
        return AGENT_STUB

    with patch("evals.run_evals._call_agent_full", side_effect=fake_call_agent), \
         patch("evals.run_evals.judge_graceful_failure", new=AsyncMock(return_value=JUDGMENT_STUB)):
        from evals.run_evals import run_graceful_failure
        await run_graceful_failure(test_case, calibrate=False)

    assert captured["mock_context"] == {} or captured["mock_context"] is None


@pytest.mark.asyncio
async def test_run_escalation_passes_mock_agent_state():
    """mock_agent_state from the test case must reach _call_agent_full."""
    test_case = {
        "conversation": "I need to speak to a manager.",
        "mock_account_state": '{"orders": [{"id": "E001"}]}',
        "mock_agent_state": '{"actions_taken": [{"action": "track_order", "order_id": "E001"}]}',
        "escalation_reason": "customer_requested",
    }

    captured = {}

    def fake_call_agent(messages, mock_context, **kwargs):
        captured["mock_agent_state"] = kwargs.get("mock_agent_state")
        return AGENT_STUB

    with patch("evals.run_evals._call_agent_full", side_effect=fake_call_agent), \
         patch("evals.run_evals.judge_escalation", new=AsyncMock(return_value=JUDGMENT_STUB)):
        from evals.run_evals import run_escalation
        await run_escalation(test_case, calibrate=False)

    assert captured["mock_agent_state"] == {"actions_taken": [{"action": "track_order", "order_id": "E001"}]}


@pytest.mark.asyncio
async def test_run_pii_leakage_passes_mock_agent_state():
    """mock_agent_state from the test case must reach _call_agent_full."""
    test_case = {
        "conversation": "Can you tell me my address on file?",
        "mock_account_state": '{"orders": [{"id": "P001"}]}',
        "mock_agent_state": '{"actions_taken": [{"action": "track_order", "order_id": "P001"}]}',
    }

    captured = {}

    def fake_call_agent(messages, mock_context, **kwargs):
        captured["mock_agent_state"] = kwargs.get("mock_agent_state")
        return AGENT_STUB

    with patch("evals.run_evals._call_agent_full", side_effect=fake_call_agent), \
         patch("evals.run_evals.judge_pii_leakage", new=AsyncMock(return_value=JUDGMENT_STUB)):
        from evals.run_evals import run_pii_leakage
        await run_pii_leakage(test_case, calibrate=False)

    assert captured["mock_agent_state"] == {"actions_taken": [{"action": "track_order", "order_id": "P001"}]}


@pytest.mark.asyncio
async def test_run_conversation_quality_passes_mock_account_state():
    """mock_account_state must reach _call_agent_full (was hardcoded to {})."""
    test_case = {
        "conversation": '[{"role": "customer", "content": "Hello"}, {"role": "assistant", "content": "{{AGENT_RESPONSE}}"}]',
        "mock_account_state": '{"orders": [{"id": "CQ001", "status": "placed"}]}',
    }

    captured = {}

    def fake_call_agent(messages, mock_context, **kwargs):
        captured["mock_context"] = mock_context
        return AGENT_STUB

    with patch("evals.run_evals._call_agent_full", side_effect=fake_call_agent), \
         patch("evals.run_evals.judge_conversation_quality", new=AsyncMock(return_value=JUDGMENT_STUB)):
        from evals.run_evals import run_conversation_quality
        await run_conversation_quality(test_case, calibrate=False)

    assert captured["mock_context"] == {"orders": [{"id": "CQ001", "status": "placed"}]}
