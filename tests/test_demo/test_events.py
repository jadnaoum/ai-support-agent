from types import SimpleNamespace as NS

from backend.demo.events import derive_events, turn_payload, error_events


def _row(action, **output):
    return NS(action=action, output_data=output)


def test_clean_kb_turn():
    out = {
        "response": "Electronics have a 14-day window.",
        "actions_taken": [
            {"service": "knowledge_service", "action": "search_kb", "query": "return window",
             "chunks_retrieved": 2, "top_similarity": 0.81},
        ],
        "retrieved_context": [
            {"title": "Electronics Return Window", "similarity": 0.81},
            {"title": "Electronics Return Window", "similarity": 0.7},
            {"title": "Product Warranties", "similarity": 0.6},
        ],
    }
    events = derive_events(out, [])
    assert [e["type"] for e in events] == ["input_guard", "kb_retrieval", "output_guard"]
    assert events[0]["verdict"] == "safe"
    assert events[1]["articles"] == ["Electronics Return Window", "Product Warranties"]
    assert events[1]["chunks"] == 2
    assert events[2]["verdict"] == "pass"


def test_kb_below_similarity_floor_reports_no_articles():
    out = {
        "response": "…",
        "actions_taken": [{"service": "knowledge_service", "action": "search_kb", "chunks_retrieved": 3, "top_similarity": 0.1}],
        "retrieved_context": [{"title": "Shipping", "similarity": 0.1}],
    }
    events = derive_events(out, [])
    assert events[1]["articles"] == []


def test_tool_rejection_then_kb_with_requires_escalation():
    out = {
        "response": "This needs a human.",
        "requires_escalation": True,
        "escalation_reason": "defective",
        "context_summary": "Customer: Galadriel…",
        "actions_taken": [
            {"service": "action_service", "action": "check_return_eligibility", "success": False,
             "order_id": "o-1", "confirmation_required": False, "result_detail": "defective"},
            {"service": "knowledge_service", "action": "search_kb", "chunks_retrieved": 1, "top_similarity": 0.7},
            {"service": "escalation_handler", "action": "escalate", "reason": "defective"},
        ],
        "action_results": [{"success": False, "reason": "defective", "requires_escalation": True}],
        "retrieved_context": [{"title": "Product Warranties", "similarity": 0.7}],
    }
    events = derive_events(out, [])
    assert [e["type"] for e in events] == ["input_guard", "tool_call", "kb_retrieval", "output_guard", "escalation"]
    tool = events[1]
    assert tool["tool"] == "check_return_eligibility" and tool["result"] == "defective"
    assert tool["requires_escalation"] is True and tool["success"] is False
    # tool-signalled escalation keeps the guarded response → guard passed
    assert events[3]["verdict"] == "pass"
    assert events[4] == {"type": "escalation", "reason": "defective", "context_summary": "Customer: Galadriel…"}


def test_confirmation_gate_pair():
    first = {
        "response": "Shall I cancel it?",
        "actions_taken": [{"service": "action_service", "action": "cancel_order", "success": False,
                           "order_id": "o-9", "confirmation_required": True, "result_detail": "confirmation_pending"}],
        "action_results": [{"success": False, "confirmation_required": True}],
    }
    events = derive_events(first, [])
    assert events[1]["confirmation_required"] is True and events[1]["result"] == "confirmation_pending"
    second = {
        "response": "Cancelled.",
        "actions_taken": [{"service": "action_service", "action": "cancel_order", "success": True,
                           "order_id": "o-9", "confirmation_required": False, "result_detail": "success"}],
        "action_results": [{"success": True, "reason": "cancelled"}],
    }
    events = derive_events(second, [])
    assert events[1]["success"] is True and events[1]["confirmation_required"] is False


def test_input_blocked_turn_has_no_output_guard():
    out = {"response": "Happy to help with your order.", "consecutive_blocks": 1, "actions_taken": []}
    events = derive_events(out, [_row("input_guard_blocked", category="prompt_injection", blocked_response="…")])
    assert events == [{"type": "input_guard", "verdict": "blocked", "reason": "prompt_injection", "strike": 1}]


def test_abusive_input_escalates_without_output_guard():
    out = {"response": "handoff", "requires_escalation": True, "escalation_reason": "abusive_input",
           "context_summary": "…", "consecutive_blocks": 0,
           "actions_taken": [{"service": "escalation_handler", "action": "escalate", "reason": "abusive_input"}]}
    events = derive_events(out, [_row("input_guard_blocked", category="abusive")])
    assert [e["type"] for e in events] == ["input_guard", "escalation"]
    assert events[0]["reason"] == "abusive"


def test_output_guard_flag_is_reported():
    out = {"response": "handoff", "requires_escalation": True, "escalation_reason": "output_guard_block",
           "actions_taken": [{"service": "escalation_handler", "action": "escalate", "reason": "output_guard_block"}]}
    events = derive_events(out, [_row("output_guard_blocked", failure_reason="unsupported_claim")])
    assert [e["type"] for e in events] == ["input_guard", "output_guard", "escalation"]
    assert events[1] == {"type": "output_guard", "verdict": "flagged", "reason": "unsupported_claim"}


def test_customer_requested_escalation_has_no_output_guard():
    out = {"response": "handoff", "requires_escalation": True, "escalation_reason": "customer_requested",
           "actions_taken": [{"service": "escalation_handler", "action": "escalate", "reason": "customer_requested"}]}
    events = derive_events(out, [])
    assert [e["type"] for e in events] == ["input_guard", "escalation"]


def test_kb_error_result_does_not_shift_tool_results():
    out = {
        "response": "…",
        "actions_taken": [
            {"service": "knowledge_service", "action": "search_kb", "chunks_retrieved": 0, "success": False},
            {"service": "action_service", "action": "track_order", "success": True, "order_id": "o-2",
             "confirmation_required": False, "result_detail": "success"},
        ],
        "action_results": [
            {"success": False, "error": "Knowledge search failed: boom", "unhandled_error": True},
            {"success": True, "reason": "tracking_info"},
        ],
    }
    events = derive_events(out, [])
    assert events[1]["type"] == "kb_retrieval" and events[1]["success"] is False
    assert events[2]["type"] == "tool_call" and events[2]["requires_escalation"] is False


def test_turn_payload_and_error_events():
    p = turn_payload(3, error_events("graph_timeout", "slow"), 90000, {"llm_calls": 2, "cost_usd": 0.01, "cost_basis": "measured"},
                     {"requires_escalation": False}, error="graph_timeout")
    assert p["index"] == 3 and p["error"] == "graph_timeout" and p["llm_calls"] == 2
    assert p["events"][0]["kind"] == "graph_timeout"
    assert p["escalation_reason"] is None
    empty = turn_payload(1, [], 10, None)
    assert empty["cost_basis"] == "unavailable" and empty["cost_usd"] is None
