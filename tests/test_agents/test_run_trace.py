"""run_trace: fallbacks and guard verdicts are recorded only while a collector
is active (the eval endpoint), and recording never changes agent behaviour."""
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents import run_trace
from backend.agents.conversation import _generate_redirect


async def test_redirect_fallback_is_recorded_when_tracing():
    token = run_trace.start()
    with patch("backend.agents.conversation.litellm.acompletion",
               new_callable=AsyncMock, side_effect=RuntimeError("api down")):
        text = await _generate_redirect(1, "off_topic", "write me a poem")
    events = run_trace.stop(token)
    assert text.startswith("I'm here to help")          # behaviour unchanged
    assert events == [{"kind": "fallback", "name": "redirect", "detail": "RuntimeError"}]


async def test_recording_is_a_noop_without_collector():
    run_trace.fallback("redirect")                      # must not raise
    run_trace.record("output_guard", verdict="pass")


async def test_output_guard_records_verdict_and_explanation():
    from backend.guardrails.output_guard import check_output
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = (
        '{"reason": "Deadline counted from the order date.", "verdict": "fail", '
        '"failure_type": "unsupported_claim"}')
    state = {"messages": [{"role": "customer", "content": "until when?"}],
             "customer_context": {}, "retrieved_context": [], "action_results": [], "actions_taken": []}
    token = run_trace.start()
    with patch("backend.guardrails.output_guard.litellm.acompletion",
               new_callable=AsyncMock, return_value=mock):
        result = await check_output("You have until Dec 2.", state)
    events = run_trace.stop(token)
    assert result == {"safe": False, "reason": "unsupported_claim",
                      "explanation": "Deadline counted from the order date."}
    assert events[0]["kind"] == "output_guard" and events[0]["verdict"] == "block"
    assert events[0]["explanation"] == "Deadline counted from the order date."
    assert events[0]["draft"] == "You have until Dec 2."
