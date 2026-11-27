"""Earlier turns' tool results and KB passages carry into the next turn's reply
step and output guard (not the classifier or routing step)."""
from backend.agents.conversation import _build_context_section
from backend.guardrails.output_guard import _build_guard_context
from backend.routers.chat import EARLIER_TURNS_KEPT, carry_earlier_context, next_turn_state

TRACK = {"success": True, "order_id": "ord-1", "status": "shipped", "estimated_delivery": "2026-12-02"}
KB = {"title": "Warranties", "category": "policy", "chunk_text": "Standard warranty: 12 months."}


def test_carry_keeps_last_turns_and_caps_kb():
    state = {"earlier_context": [], "action_results": [TRACK], "retrieved_context": [KB, KB, KB]}
    earlier = carry_earlier_context(state)
    assert earlier == [{"results": [TRACK], "kb": [KB, KB]}]
    for _ in range(5):
        earlier = carry_earlier_context({"earlier_context": earlier, "action_results": [TRACK]})
    assert len(earlier) == EARLIER_TURNS_KEPT


def test_turn_without_results_adds_nothing():
    assert carry_earlier_context({"earlier_context": [], "action_results": []}) == []
    assert next_turn_state({"action_results": [TRACK]})["earlier_context"][0]["results"] == [TRACK]


def test_reply_step_sees_earlier_results_routing_step_does_not():
    state = {"earlier_context": [{"results": [TRACK], "kb": [KB]}], "customer_context": {}}
    reply = _build_context_section(state, with_earlier=True)
    assert "2026-12-02" in reply and "12 months" in reply
    assert "2026-12-02" not in _build_context_section(state, with_orders=False)


def test_guard_sees_agent_replies_and_earlier_results():
    state = {
        "messages": [{"role": "customer", "content": "where is it?"},
                     {"role": "agent", "content": "Arriving Dec 2."},
                     {"role": "customer", "content": "remind me when?"}],
        "earlier_context": [{"results": [TRACK], "kb": [KB]}],
        "customer_context": {},
    }
    ctx = _build_guard_context("It's arriving Dec 2.", state)
    assert "Agent: Arriving Dec 2." in ctx
    assert "results_from_earlier_turns" in ctx and "2026-12-02" in ctx
    assert "12 months" in ctx


def test_reply_step_and_guard_see_todays_date():
    from datetime import datetime, timezone
    from backend.agents.order_context import today_line
    line = today_line()
    now = datetime.now(timezone.utc)
    assert line == f"Today's date: {now:%Y-%m-%d} ({now:%A})"
    state = {"earlier_context": [], "customer_context": {}, "messages": []}
    assert line in _build_context_section(state, with_earlier=True)
    assert line not in _build_context_section(state, with_orders=False)
    assert line in _build_guard_context("ok", state)
