from backend.demo.checks import compute_checks, pii_hits


def _turn(events, response="ok"):
    return {"events": events, "response": response}


IG = {"type": "input_guard", "verdict": "safe"}
OG = {"type": "output_guard", "verdict": "pass"}


def _by_id(checks):
    return {c["id"]: c for c in checks}


def test_empty_conversation_is_na():
    assert {c["status"] for c in compute_checks([])} == {"na"}


def test_all_pass_simple():
    c = _by_id(compute_checks([_turn([IG, OG]), _turn([IG, OG])]))
    assert c["inputs_screened"]["status"] == "pass" and c["inputs_screened"]["text"] == "All 2 inputs screened"
    assert c["responses_verified"]["status"] == "pass" and "2 verified" in c["responses_verified"]["text"]
    assert c["confirmation_before_change"]["text"].endswith("none attempted")
    assert c["policy_grounded"]["status"] == "na"
    assert c["no_pii"]["status"] == "pass"


def test_blocked_input_counts_as_screened_and_unverified():
    c = _by_id(compute_checks([
        _turn([{"type": "input_guard", "verdict": "blocked", "reason": "off_topic"}]),
        _turn([IG, OG]),
    ]))
    assert c["inputs_screened"]["text"] == "All 2 inputs screened · 1 blocked"
    assert c["responses_verified"]["status"] == "pass"
    assert "1 verified, 1 redirect/handoff not guarded" in c["responses_verified"]["text"]


def test_flagged_response_fails_verification():
    c = _by_id(compute_checks([_turn([IG, {"type": "output_guard", "verdict": "flagged", "reason": "hallucinated_id"}])]))
    assert c["responses_verified"] == {"id": "responses_verified", "status": "fail",
                                       "text": "1 response flagged by verification"}


def test_confirmation_gate_pass_and_fail():
    ask = {"type": "tool_call", "tool": "cancel_order", "order_id": "o-1", "confirmation_required": True, "success": False}
    do = {"type": "tool_call", "tool": "cancel_order", "order_id": "o-1", "confirmation_required": False, "success": True}
    c = _by_id(compute_checks([_turn([IG, ask, OG]), _turn([IG, do, OG])]))
    assert c["confirmation_before_change"]["status"] == "pass"
    assert c["confirmation_before_change"]["text"].endswith("1 confirmed")

    other = {**do, "order_id": "o-2"}
    c = _by_id(compute_checks([_turn([IG, ask, OG]), _turn([IG, other, OG])]))
    assert c["confirmation_before_change"]["status"] == "fail"
    assert c["confirmation_before_change"]["text"] == "1 state change executed without confirmation"


def test_policy_grounding_counts_rejections():
    reject = {"type": "tool_call", "tool": "check_return_eligibility", "success": False, "confirmation_required": False}
    kb = {"type": "kb_retrieval", "articles": ["Returns"], "chunks": 1}
    c = _by_id(compute_checks([_turn([IG, reject, kb, OG]), _turn([IG, reject, OG])]))
    assert c["policy_grounded"]["status"] == "fail"
    assert c["policy_grounded"]["text"] == "Policy answers grounded in KB for 1 of 2 policy turns"
    c = _by_id(compute_checks([_turn([IG, reject, kb, OG])]))
    assert c["policy_grounded"]["status"] == "pass"


def test_pii_detection():
    assert pii_hits("Email me at bob@example.com") == ["email"]
    assert pii_hits("Email me at bob@example.com", own_email="bob@example.com") == []
    assert pii_hits("Call +1 (415) 555-0142 tomorrow") == ["phone"]
    assert pii_hits("Card 4111 1111 1111 1111") == ["card"]
    assert pii_hits("SSN 123-45-6789") == ["ssn"]
    # order UUIDs and amounts are not PII
    assert pii_hits("Order 11111111-0000-0000-0000-000000000001 totals $1,299.99 and refunds take 3-5 days") == []


def test_pii_check_in_conversation():
    c = _by_id(compute_checks([_turn([IG, OG], response="Sent to you@x.io")], own_email="me@shire.demo"))
    assert c["no_pii"] == {"id": "no_pii", "status": "fail", "text": "Personal data patterns in 1 response"}


def test_policy_grounding_ignores_validation_gates_and_failures():
    ask_id = {"type": "tool_call", "tool": "cancel_order", "result": "order_id_required", "success": False, "confirmation_required": False}
    failed = {"type": "tool_call", "tool": "check_cancel_eligibility", "result": "failed", "success": False, "confirmation_required": False}
    c = _by_id(compute_checks([_turn([IG, ask_id, OG]), _turn([IG, failed])]))
    assert c["policy_grounded"]["status"] == "na"
