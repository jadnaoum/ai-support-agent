"""
Tests for the output guardrail (rule-based, no LLM).
"""
import uuid
from backend.guardrails.output_guard import check_output


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


# ---------------------------------------------------------------------------
# Normal responses pass
# ---------------------------------------------------------------------------

def test_clean_response_is_safe():
    result = check_output("I'd be happy to help with your return.", make_state())
    assert result["safe"] is True


def test_future_tense_cancellation_is_safe():
    """'I'll cancel your order' is a promise, not a claim — should pass."""
    result = check_output("I'll cancel your order for you right now.", make_state())
    assert result["safe"] is True


def test_conditional_language_is_safe():
    result = check_output("Once you initiate the return, we will process your refund.", make_state())
    assert result["safe"] is True


# ---------------------------------------------------------------------------
# Impossible promise detection
# ---------------------------------------------------------------------------

def test_ive_cancelled_without_tool_is_blocked():
    result = check_output("I've cancelled your order successfully.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "impossible_promise"


def test_ive_processed_refund_without_tool_is_blocked():
    result = check_output("I've processed your refund of $50.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "impossible_promise"


def test_order_has_been_cancelled_without_tool_is_blocked():
    result = check_output("Your order has been cancelled as requested.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "impossible_promise"


def test_refund_has_been_processed_without_tool_is_blocked():
    result = check_output("Your refund has been processed and will appear in 3-5 days.", make_state())
    assert result["safe"] is False
    assert result["reason"] == "impossible_promise"


def test_ive_cancelled_with_cancel_tool_is_safe():
    state = make_state(actions_taken=[{"service": "action_service", "action": "cancel_order"}])
    result = check_output("I've cancelled your order successfully.", state)
    assert result["safe"] is True


def test_refund_claim_with_refund_tool_is_safe():
    state = make_state(actions_taken=[{"service": "action_service", "action": "process_refund"}])
    result = check_output("I've processed your refund.", state)
    assert result["safe"] is True


# ---------------------------------------------------------------------------
# Order ID hallucination detection
# ---------------------------------------------------------------------------

def test_response_with_no_uuids_is_safe():
    result = check_output("Your order is on its way!", make_state())
    assert result["safe"] is True


def test_response_echoing_customer_uuid_is_safe():
    known_id = str(uuid.uuid4())
    state = make_state(
        messages=[{"role": "customer", "content": f"Track my order {known_id}"}]
    )
    result = check_output(f"I can see order {known_id} is currently being shipped.", state)
    assert result["safe"] is True


def test_response_with_uuid_from_action_result_is_safe():
    known_id = str(uuid.uuid4())
    state = make_state(
        action_results=[{"order_id": known_id, "status": "delivered"}],
        actions_taken=[{"service": "action_service", "action": "track_order"}],
    )
    result = check_output(f"Your order {known_id} was delivered yesterday.", state)
    assert result["safe"] is True


def test_response_with_fabricated_uuid_is_blocked():
    fabricated_id = str(uuid.uuid4())  # not present in state anywhere
    result = check_output(
        f"Your order {fabricated_id} has been placed.",
        make_state(),
    )
    assert result["safe"] is False
    assert result["reason"] == "hallucinated_id"


def test_response_with_uuid_from_customer_context_is_safe():
    known_id = str(uuid.uuid4())
    state = make_state(
        customer_context={
            "name": "Alice",
            "recent_orders": [{"order_id": known_id, "status": "shipped", "total": 50.0, "placed_at": None}],
        }
    )
    result = check_output(f"I can see order {known_id} is on its way.", state)
    assert result["safe"] is True
