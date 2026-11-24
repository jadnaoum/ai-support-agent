"""
Recent orders in the agent context and output guard evidence: one renderer
(format_recent_orders) feeds both, and the eval endpoint builds the same list
from mock fixtures.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents.conversation import _build_context_section
from backend.agents.mock_tools import mock_recent_orders
from backend.agents.order_context import format_recent_orders

LAMP = {
    "order_id": "3f2a9c1e-0000-4000-8000-00000000aaaa", "status": "shipped",
    "total": 49.99, "placed_at": "2026-11-02T10:00:00+00:00",
    "items": [{"product": "Desk lamp", "quantity": 1}],
}
BOOTS = {
    "order_id": "91c0e4aa-0000-4000-8000-00000000bbbb", "status": "delivered",
    "total": 89.0, "placed_at": "2026-10-18T09:00:00+00:00",
    "items": [{"product": "Leather boots", "quantity": 1}, {"product": "Wool socks", "quantity": 2}],
}


def test_renders_one_line_per_order_newest_first():
    block = format_recent_orders({"recent_orders": [LAMP, BOOTS]})
    assert block.splitlines() == [
        "Recent orders (newest first):",
        f"- {LAMP['order_id']} | shipped | placed 2026-11-02 | $49.99 | Desk lamp (x1)",
        f"- {BOOTS['order_id']} | delivered | placed 2026-10-18 | $89.00 | Leather boots (x1), Wool socks (x2)",
    ]


def test_empty_list_says_none_but_missing_list_is_omitted():
    assert format_recent_orders({"name": "A", "recent_orders": []}) == "Recent orders: none on this account"
    assert format_recent_orders({"name": "A"}) == ""
    assert format_recent_orders(None) == ""


def test_context_section_shows_orders_under_customer_name():
    section = _build_context_section({"customer_context": {"name": "Triss", "recent_orders": [LAMP]}})
    assert section.index("Customer: Triss") < section.index("Recent orders (newest first):")
    assert "Desk lamp (x1)" in section


async def test_output_guard_receives_same_order_block():
    from backend.guardrails.output_guard import check_output
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = '{"verdict": "pass", "failure_type": "none"}'
    state = {"messages": [{"role": "customer", "content": "where's my lamp?"}],
             "customer_context": {"name": "Triss", "recent_orders": [LAMP]},
             "retrieved_context": [], "action_results": [], "actions_taken": []}
    with patch("backend.guardrails.output_guard.litellm.acompletion",
               new_callable=AsyncMock, return_value=mock) as llm:
        await check_output("Your desk lamp from Nov 2 has shipped.", state)
    prompt = llm.call_args.kwargs["messages"][0]["content"]
    assert "<customer_orders>\n" + format_recent_orders(state["customer_context"]) in prompt


def test_mock_recent_orders_matches_production_shape():
    fixture = {"orders": [
        {"id": "ord-1", "status": "shipped", "total": 49.99, "placed_date": "2026-11-02",
         "items": [{"name": "Desk lamp", "category": "home_goods", "qty": 1}]},
        {"id": "ord-2", "status": "delivered", "item": "Boots"},
        {"id": "ord-3"},
    ]}
    recent = mock_recent_orders(fixture, limit=2)
    assert recent == [
        {"order_id": "ord-1", "status": "shipped", "total": 49.99, "placed_at": "2026-11-02",
         "items": [{"product": "Desk lamp", "quantity": 1}]},
        {"order_id": "ord-2", "status": "delivered", "total": 0.0, "placed_at": None,
         "items": [{"product": "Boots", "quantity": 1}]},
    ]


async def test_classifier_sees_order_block_after_account_holder():
    from backend.agents.conversation import _classify_intent
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = '{"intent": "general", "confidence": 0.9}'
    state = {"messages": [{"role": "customer", "content": "my lamp is broken"}],
             "customer_context": {"name": "Triss", "recent_orders": [LAMP, BOOTS]}}
    with patch("backend.agents.conversation.litellm.acompletion",
               new_callable=AsyncMock, return_value=mock) as llm:
        await _classify_intent(state, {})
    blocks = llm.call_args.kwargs["messages"][0]["content"]
    assert "cache_control" in blocks[0]
    assert blocks[1]["text"].strip() == "Account holder: Triss"
    assert blocks[2]["text"].strip() == format_recent_orders(state["customer_context"], identity_only=True)
    # identity only: no status / total the classifier could match eligibility on
    assert "shipped" not in blocks[2]["text"] and "$" not in blocks[2]["text"]
    assert "Desk lamp (x1)" in blocks[2]["text"]


def test_candidate_orders_end_the_turn():
    from backend.agents.conversation import _service_needs_loop
    state = {"actions_taken": [{"service": "action_service", "action": "track_order"}],
             "action_results": [{"success": False, "reason": "order_id_required",
                                 "candidate_orders": [LAMP, BOOTS]}]}
    assert _service_needs_loop(state) is False
    # without candidates (cancel/return → check tool narrows by eligibility) the loop still runs
    state["action_results"] = [{"success": False, "reason": "order_id_required",
                                "available_action": "check_cancel_eligibility"}]
    assert _service_needs_loop(state) is True


def test_identity_only_rendering():
    assert format_recent_orders({"recent_orders": [LAMP]}, identity_only=True).splitlines() == [
        "Recent orders (newest first):",
        f"- {LAMP['order_id']} | placed 2026-11-02 | Desk lamp (x1)",
    ]


async def test_loop_decision_gets_no_order_list():
    from backend.agents.conversation import _classify_next_step
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = '{"next": "respond"}'
    state = {"messages": [{"role": "customer", "content": "cancel my order"}],
             "customer_context": {"name": "Triss", "recent_orders": [LAMP, BOOTS]},
             "retrieved_context": [], "actions_taken": [],
             "action_results": [{"success": False, "reason": "order_id_required",
                                 "available_action": "check_cancel_eligibility"}]}
    with patch("backend.agents.conversation.litellm.acompletion",
               new_callable=AsyncMock, return_value=mock) as llm:
        await _classify_next_step(state)
    sent = str(llm.call_args.kwargs["messages"])
    assert LAMP["order_id"] not in sent and "Recent orders" not in sent


def test_settled_which_order_note_is_dropped():
    from backend.agents.conversation import _result_guidance_notes
    guidance = {"order_id_required": "ASK-WHICH", "eligible": "ELIGIBLE-NOTE"}
    with patch("backend.agents.conversation.get_response_guidance", return_value=guidance):
        # still the last result → the customer must be asked
        assert _result_guidance_notes([{"reason": "order_id_required"}]) == ["ASK-WHICH"]
        # a later tool ran (the loop's eligibility check) → note dropped
        assert _result_guidance_notes(
            [{"reason": "order_id_required"}, {"success": True, "eligible_orders": []}]
        ) == []


def test_tracking_info_guidance_exists():
    from prompts.loader import get_response_guidance
    assert "placed_at" in get_response_guidance()["tracking_info"]
