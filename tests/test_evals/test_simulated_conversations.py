"""Simulated Conversations runner: turn-state carry, stop conditions,
programmatic escalation check, run-twice aggregation. LLMs and the agent are faked."""
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import evals.run_evals as re_
from evals.judges import simulated as sim


def test_customer_sees_products_and_age_but_no_ids():
    placed = (date.today() - timedelta(days=150)).isoformat()
    text = sim.orders_for_customer({"orders": [{"id": "secret-id", "status": "delivered", "placed_at": placed,
                                                "items": [{"name": "Ninja Professional Blender"}]}]})
    assert "Ninja Professional Blender" in text and "150 days ago" in text and "secret-id" not in text


def test_expected_escalation_check():
    case = lambda v: {"expected_escalation": v}
    assert sim.check_expected_escalation(case(""), True, "defective") is None            # not checked
    assert sim.check_expected_escalation(case("none"), False, "") is None
    assert "expected no escalation" in sim.check_expected_escalation(case("none"), True, "threat")
    assert sim.check_expected_escalation(case("defective|carrier_claim_eligible"), True, "defective") is None
    assert "never escalated" in sim.check_expected_escalation(case("defective"), False, "")
    assert sim.check_expected_escalation(case("llm_escalation_intent|none"), False, "") is None
    assert "but got threat" in sim.check_expected_escalation(case("llm_escalation_intent|none"), True, "threat")


def test_turn_summary_reports_tools_blocks_and_fallbacks():
    line = sim.turn_summary(2, {
        "inferred_intent": "action_request", "requires_escalation": False,
        "actions_taken": [{"service": "action_service", "action": "track_order", "params": {"order_id": None},
                           "result_detail": "order_id_required"}],
        "trace": [{"kind": "output_guard", "verdict": "block", "failure_type": "unsupported_claim", "explanation": "x"},
                  {"kind": "fallback", "name": "redirect"}]})
    assert "Turn 2" in line and "order_id_required" in line and "unsupported_claim" in line and "redirect" in line


async def test_conversation_carries_turn_state_and_stops_on_end():
    customer = iter(["hi, where's my lamp?", "thanks!", "[END]"])
    sent_states = []

    def fake_agent(messages, mock_context, **kw):
        sent_states.append(kw.get("mock_agent_state"))
        return {"response": "It shipped.", "actions_taken": [], "trace": [], "requires_escalation": False,
                "turn_state": {"consecutive_small_talk": len(sent_states)}}

    with patch.object(sim, "next_customer_message", new=AsyncMock(side_effect=lambda *a: (next(customer), 0.0))), \
         patch.object(re_, "_call_agent_full", side_effect=fake_agent), \
         patch.object(re_, "SIM_TURN_DELAY_S", 0), \
         patch.object(sim, "judge_simulated_conversation",
                      new=AsyncMock(return_value={"verdict": "pass", "score": 1.0, "reasoning": "ok"})):
        resp, judgment = await re_._simulate_once({"persona": "p", "goal": "g", "expected_escalation": "none"}, {}, "SC-T", "")
    assert sent_states == [None, {"consecutive_small_talk": 1}]      # production-style carry
    assert resp["response"].count("Customer:") == 2 and judgment["verdict"] == "pass"


async def test_wrong_escalation_fails_without_calling_the_judge():
    def fake_agent(messages, mock_context, **kw):
        return {"response": "Passing you to a colleague.", "actions_taken": [], "trace": [],
                "requires_escalation": True, "escalation_reason": "threat", "turn_state": None}
    judge = AsyncMock()
    with patch.object(sim, "next_customer_message", new=AsyncMock(return_value=("refund now!", 0.0))), \
         patch.object(re_, "_call_agent_full", side_effect=fake_agent), \
         patch.object(sim, "judge_simulated_conversation", new=judge):
        _, judgment = await re_._simulate_once({"persona": "p", "goal": "g", "expected_escalation": "none"}, {}, "SC-T", "")
    assert judgment["failure_reason"] == "wrong_escalation"
    judge.assert_not_awaited()


async def test_runs_twice_and_fails_if_any_run_fails():
    outcomes = iter([({"response": "a", "trace": []}, {"verdict": "pass", "reasoning": "fine", "cost_usd": 0.1}),
                     ({"response": "b", "trace": []}, {"verdict": "fail", "reasoning": "bad", "failure_reason": "poor_tone", "cost_usd": 0.1})])
    with patch.object(re_, "_simulate_once", new=AsyncMock(side_effect=lambda *a: next(outcomes))):
        resp, judgment = await re_.run_simulated_conversation({"runs": 2}, False, "SC-T")
    assert judgment["verdict"] == "fail" and judgment["failure_reason"] == "poor_tone"
    assert resp["response"] == "b" and "run 1: pass" in judgment["reasoning"]


def test_executed_writes_update_the_fixture():
    mock = {"orders": [{"id": "o1", "status": "processing"}, {"id": "o2", "status": "delivered"}]}
    sim.apply_writes(mock, [
        {"action": "cancel_order", "result_detail": "confirmation_pending", "order_id": "o1"},   # not executed
        {"action": "initiate_return", "result_detail": "success", "order_id": "o2"},
    ])
    assert [o["status"] for o in mock["orders"]] == ["processing", "return_in_progress"]
    sim.apply_writes(mock, [{"action": "cancel_order", "result_detail": "success", "order_id": "o1"}])
    assert mock["orders"][0]["status"] == "cancelled"


async def test_each_run_starts_from_the_fixture_as_written():
    fixture = {"orders": [{"id": "o1", "status": "processing"}]}
    customer = iter(["cancel it", "[END]"])

    def fake_agent(messages, mock_context, **kw):
        return {"response": "Cancelled.", "trace": [], "requires_escalation": False, "turn_state": {},
                "actions_taken": [{"action": "cancel_order", "result_detail": "success", "order_id": "o1"}]}
    with patch.object(sim, "next_customer_message", new=AsyncMock(side_effect=lambda *a: (next(customer), 0.0))), \
         patch.object(re_, "_call_agent_full", side_effect=fake_agent), \
         patch.object(re_, "SIM_TURN_DELAY_S", 0), \
         patch.object(sim, "judge_simulated_conversation", new=AsyncMock(return_value={"verdict": "pass"})):
        await re_._simulate_once({"persona": "p", "goal": "g"}, fixture, "SC-T", "")
    assert fixture["orders"][0]["status"] == "processing"      # caller's fixture untouched


async def test_judges_retry_empty_replies():
    from evals.judges import behavioral
    calls = iter([{"verdict": "fail", "failure_reason": "judge_error", "reasoning": "Judge call failed: Expecting value"},
                  {"verdict": "pass", "score": 1.0, "reasoning": "ok", "failure_reason": None}])
    with patch.object(behavioral, "_llm_judge_once", new=AsyncMock(side_effect=lambda *a: next(calls))):
        assert (await behavioral._llm_judge("p"))["verdict"] == "pass"


def test_simulator_no_longer_defaults_to_haiku():
    assert "haiku" not in sim.SIM_CUSTOMER_MODEL.lower() or __import__("os").environ.get("EVAL_SIM_MODEL")


async def test_guard_api_error_mid_conversation_is_infra():
    # SC-008 run 2: the output guard's API call failed → fail closed → handoff.
    def fake_agent(messages, mock_context, **kw):
        return {"response": "Passing you to a colleague.", "actions_taken": [], "requires_escalation": True,
                "escalation_reason": "output_guard_block", "turn_state": None,
                "trace": [{"kind": "fallback", "name": "output_guard_error"}]}
    with patch.object(sim, "next_customer_message", new=AsyncMock(return_value=("where's my refund?", 0.0))), \
         patch.object(re_, "_call_agent_full", side_effect=fake_agent):
        _, judgment = await re_._simulate_once({"persona": "p", "goal": "g", "expected_escalation": "none"}, {}, "SC-T", "")
    assert judgment["verdict"] == "fail" and judgment["failure_reason"] == "guard_error_infra"


async def test_real_failure_outranks_infra_in_the_other_run():
    infra = ({"response": "i", "trace": []}, {"verdict": "fail", "reasoning": "guard", "failure_reason": "guard_error_infra"})
    real = ({"response": "r", "trace": []}, {"verdict": "fail", "reasoning": "bad", "failure_reason": "wrong_escalation"})
    passed = ({"response": "p", "trace": []}, {"verdict": "pass", "reasoning": "fine"})
    for runs, reason, shown in [([infra, real], "wrong_escalation", "r"), ([passed, infra], "guard_error_infra", "i")]:
        outcomes = iter(runs)
        with patch.object(re_, "_simulate_once", new=AsyncMock(side_effect=lambda *a: next(outcomes))):
            resp, judgment = await re_.run_simulated_conversation({"runs": 2}, False, "SC-T")
        assert judgment["failure_reason"] == reason and resp["response"] == shown


def test_simulator_sees_the_chat_as_its_own_turns():
    chat = sim.sim_chat_messages("p", "g", "o", [{"role": "customer", "content": "hi"},
                                                  {"role": "agent", "content": "Hello! How can I help?"}])
    assert [m["role"] for m in chat] == ["system", "user", "assistant", "user"]
    assert chat[2]["content"] == "hi" and "{transcript}" not in chat[0]["content"]


async def test_simulator_retries_when_it_writes_as_the_agent():
    def reply(text):
        r = AsyncMock(); r.choices = [AsyncMock()]; r.choices[0].message.content = text
        return r
    replies = iter([reply("Assistant: Sure! Your lamp has shipped."), reply("Customer: ok thanks")])
    with patch.object(sim.litellm, "acompletion", new=AsyncMock(side_effect=lambda **k: next(replies))), \
         patch.object(sim.litellm, "completion_cost", return_value=0.0):
        text, _ = await sim.next_customer_message("p", "g", "o", [])
    assert text == "ok thanks"


def test_turn_summary_shows_tool_facts_like_deadlines():
    line = sim.turn_summary(1, {"inferred_intent": "action_request", "actions_taken": [], "trace": [],
                                "action_results": [{"reason": "outside_return_window", "eligible": False,
                                                    "return_deadline": "2026-11-21", "noise": "x"}]})
    assert '"return_deadline": "2026-11-21"' in line and "noise" not in line
