"""
Simulated Conversations sheet — simulated customer + whole-conversation judge.

The customer is played by an eval-only LLM (sim_customer_prompt, Haiku); the
agent is the real one via /api/chat/test. The judge (simulated_conversation_prompt)
scores the whole transcript and is given structural facts per turn (tools,
escalations, guard blocks, fallbacks) that it must trust over the text.
Both prompts are owner-authored in prompts/eval_rubrics.yaml.
"""
import json
import os
import sys
from datetime import date

import litellm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # noqa: E402

from evals.config import JUDGE_MODEL_BEHAVIORAL, JUDGE_MODEL_CALIBRATION  # noqa: E402
from prompts.loader import get_prompt  # noqa: E402

# Sonnet, not Haiku: in eval_gaps_v1 the Haiku simulator sometimes wrote the
# assistant's line instead of the customer's (SC-003 turn 4, SC-006 turn 4),
# and the agent then acted on text the customer never sent.
SIM_CUSTOMER_MODEL = os.environ.get("EVAL_SIM_MODEL", JUDGE_MODEL_BEHAVIORAL)
END_TOKEN = "[END]"

# PROMPTS — edit in prompts/eval_rubrics.yaml
_SIM_PROMPT = get_prompt("sim_customer_prompt")
_SIM_OPENING = get_prompt("sim_customer_opening").strip()
_JUDGE_PROMPT = get_prompt("simulated_conversation_prompt")


def orders_for_customer(mock: dict) -> str:
    """The customer's view of their orders: products, roughly when, status. No IDs."""
    lines = []
    for o in mock.get("orders") or []:
        names = ", ".join(i.get("name", "") for i in o.get("items") or []) or o.get("item", "an item")
        when = ""
        try:
            days = (date.today() - date.fromisoformat(str(o.get("placed_at"))[:10])).days
            when = f"ordered {days} days ago" if days > 1 else "ordered in the last couple of days"
        except Exception:
            pass
        lines.append(f"- {names}" + (f" — {when}" if when else "") + f" (status: {o.get('status', 'unknown')})")
    return "\n".join(lines) or "(no orders)"


# Order status after an executed write — the mock tools read the fixture fresh
# every turn, so without this a cancelled order still looks cancellable on the
# next simulated turn (SC-006 in eval_gaps_v1).
_WRITE_STATUS = {"cancel_order": "cancelled", "initiate_return": "return_in_progress"}


def apply_writes(mock: dict, actions_taken: list) -> None:
    for a in actions_taken:
        status = _WRITE_STATUS.get(a.get("action"))
        if status and a.get("result_detail") == "success" and a.get("order_id"):
            for o in mock.get("orders") or []:
                if str(o.get("id")) == str(a["order_id"]):
                    o["status"] = status


def render_transcript(messages: list) -> str:
    if not messages:
        return "(no messages yet — you start the conversation)"
    who = {"customer": "Customer", "agent": "Assistant"}
    return "\n".join(f"{who.get(m['role'], m['role'])}: {m['content']}" for m in messages)


def sim_chat_messages(persona: str, goal: str, orders: str, messages: list) -> list:
    """The simulator plays the customer as the MODEL's side of a real chat:
    its own earlier lines are assistant turns, the agent's replies are user
    turns. Pasting the chat as one transcript ending on the agent's line made
    it sometimes continue as the agent (SC-003)."""
    chat = [{"role": "system", "content": _SIM_PROMPT.format(persona=persona, goal=goal, orders=orders)},
            {"role": "user", "content": _SIM_OPENING}]
    for m in messages:
        role = "assistant" if m["role"] == "customer" else "user"
        if chat[-1]["role"] == role:          # keep strict alternation
            chat[-1]["content"] += "\n\n" + m["content"]
        else:
            chat.append({"role": role, "content": m["content"]})
    return chat


async def next_customer_message(persona: str, goal: str, orders: str, messages: list) -> "tuple[str, float]":
    chat, text, cost = sim_chat_messages(persona, goal, orders, messages), "", 0.0
    for _ in range(2):                         # one retry if it still writes as the agent
        result = await litellm.acompletion(model=SIM_CUSTOMER_MODEL, messages=chat,
                                           stream=False, timeout=60, num_retries=3)
        try:
            cost += litellm.completion_cost(completion_response=result)
        except Exception:
            pass
        text = (result.choices[0].message.content or "").strip()
        if text.lower().startswith("customer:"):
            text = text.split(":", 1)[1].strip()
        if not text.lower().startswith("assistant:"):
            break
    return text, cost


# Dated / numeric facts a tool result can carry: the judge must see them, or a
# date the agent correctly quoted from the tool reads as invented (SC-002).
_RESULT_FACT_KEYS = ("reason", "eligible", "status", "return_window_days", "return_window_starts",
                     "return_deadline", "in_return_window", "in_warranty", "warranty_months",
                     "warranty_starts", "warranty_expires", "estimated_delivery", "tracking_number",
                     "refund_amount", "amount", "requires_escalation")


def turn_summary(turn: int, resp: dict) -> str:
    """One line of structural facts for a turn, from the endpoint response."""
    tools = [f"{a.get('action')}({json.dumps(a.get('params') or {})}) -> {a.get('result_detail')}"
             for a in resp.get("actions_taken") or [] if a.get("service") == "action_service"]
    kb = any(a.get("service") == "knowledge_service" for a in resp.get("actions_taken") or [])
    trace = resp.get("trace") or []
    blocks = [f"{t.get('failure_type')}: {t.get('explanation', '')[:160]}"
              for t in trace if t.get("kind") == "output_guard" and t.get("verdict") == "block"]
    fallbacks = [t.get("name") for t in trace if t.get("kind") == "fallback"]
    parts = [f"Turn {turn}: intent={resp.get('inferred_intent') or '-'}",
             f"tools={tools or 'none'}", f"kb_searched={kb}"]
    facts = [{k: r[k] for k in _RESULT_FACT_KEYS if r.get(k) not in (None, "", [])}
             for r in resp.get("action_results") or [] if isinstance(r, dict)]
    facts = [f for f in facts if f]
    if facts:
        parts.append(f"tool_facts={json.dumps(facts)}")
    if resp.get("input_guard_blocked"):
        parts.append(f"input_guard_blocked={resp.get('input_guard_reason')}")
    if blocks:
        parts.append(f"output_guard_blocked=[{'; '.join(blocks)}]")
    if fallbacks:
        parts.append(f"fallbacks_fired={fallbacks}")
    parts.append(f"escalated={resp.get('escalation_reason') if resp.get('requires_escalation') else 'no'}")
    return ", ".join(parts)


def check_expected_escalation(test_case: dict, escalated: bool, reason: str) -> "str | None":
    """Programmatic escalation check (system enum codes, pipe-separated; 'none' =
    must not escalate; blank = not checked). Returns a failure description or None."""
    expected = str(test_case.get("expected_escalation") or "").strip().lower()
    if not expected:
        return None
    if expected == "none":
        return f"expected no escalation but the agent escalated ({reason})" if escalated else None
    accepted = {t.strip() for t in expected.split("|") if t.strip()}
    if not escalated:
        # "none" among alternatives = not escalating is also acceptable
        return None if "none" in accepted else f"expected escalation ({expected}) but the agent never escalated"
    if reason not in accepted:
        return f"expected escalation reason {expected} but got {reason}"
    return None


async def judge_simulated_conversation(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    """Retries when the judge returns an empty / non-JSON reply."""
    for _ in range(3):
        result = await _judge_once(test_case, agent_response, calibrate)
        if result.get("failure_reason") != "judge_error":
            return result
    return result


async def _judge_once(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _JUDGE_PROMPT.format(
        persona=test_case.get("persona", ""),
        goal=test_case.get("goal", ""),
        mock_account_state=test_case.get("mock_account_state", "{}"),
        expected_behavior=test_case.get("expected_behavior", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        structural_summary=agent_response.get("structural_summary") or "(not recorded)",
        transcript=agent_response.get("response", ""),
    )
    model = JUDGE_MODEL_CALIBRATION if calibrate else JUDGE_MODEL_BEHAVIORAL
    try:
        result = await litellm.acompletion(model=model, messages=[{"role": "user", "content": prompt}],
                                           stream=False, timeout=90, num_retries=3)
        try:
            cost = litellm.completion_cost(completion_response=result)
        except Exception:
            cost = 0.0
        raw = result.choices[0].message.content.strip()
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start:end + 1])
        verdict = "pass" if parsed.get("verdict") == "pass" else "fail"
        reasoning = parsed.get("reasoning", "No reasoning provided.")
        if verdict == "fail" and parsed.get("failure_turn") is not None:
            reasoning = f"[turn {parsed.get('failure_turn')}] {reasoning}"
        return {"verdict": verdict, "score": 1.0 if verdict == "pass" else 0.0, "reasoning": reasoning,
                "failure_reason": parsed.get("failure_reason") if verdict == "fail" else None, "cost_usd": cost}
    except Exception as e:
        return {"verdict": "fail", "score": 0.0, "reasoning": f"Judge error: {e}",
                "failure_reason": "judge_error", "cost_usd": 0.0}
