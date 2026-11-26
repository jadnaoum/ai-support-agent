"""
Output guardrail — runs after the conversation agent generates a response,
before the response is sent to the customer.

LLM-based check using LITELLM_GUARD_MODEL (Sonnet by default):
Detects hallucinations, unsupported claims, and policy violations that
regex patterns cannot catch reliably. Checks for:
  1. unsupported_claim   — impossible promises, speculative guarantees, or invented policy details
  2. hallucinated_id     — fabricated order ID / tracking number / UUID
  3. system_disclosure   — leaking system prompt or internal instructions
  4. cross_customer_leak — mentioning another customer's data

Returns a dict:
  {"safe": True}
  {"safe": False, "reason": str}

Fails closed on any LLM or parse error — blocks the response rather than
letting unvalidated content through to the customer.
"""
import json
import logging
import re
import uuid as uuid_mod

import litellm

from backend.demo.sink import emit

from backend.agents import run_trace
from backend.agents.order_context import format_recent_orders
from backend.agents.state import AgentState
from backend.config import get_settings
from prompts.loader import get_prompt

settings = get_settings()
logger = logging.getLogger(__name__)

# PROMPT — edit in prompts/production.yaml
OUTPUT_GUARD_PROMPT = get_prompt("output_guard_prompt")

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _build_guard_context(response: str, state: AgentState) -> str:
    """Assemble the filled OUTPUT_GUARD_PROMPT string from state."""
    # Recent customer messages (last 6)
    customer_msgs = [
        m["content"] for m in (state.get("messages") or [])
        if m.get("role") == "customer"
    ]
    conversation = "\n".join(f"- {m}" for m in customer_msgs[-6:]) or "(none)"

    # Tools called this turn + their results
    actions = state.get("actions_taken") or []
    action_results = state.get("action_results") or []
    tools_payload = {
        "tools_called": [
            {"tool": a.get("action", ""), "service": a.get("service", "")}
            for a in actions
        ],
        "results": action_results,
    }
    tools_called = json.dumps(tools_payload, indent=2)

    # IDs legitimately present in this conversation's context, mapped to a
    # label ("" = unlabeled). The customer's name and email must be labeled:
    # unlabeled, the guard read the customer's name as the assistant's own
    # name and failed "I don't have a name" as unsupported_claim. The first
    # label recorded for a value wins (setdefault).
    ids: "dict[str, str]" = {}
    ctx = state.get("customer_context") or {}
    # Include the customer's own name and email so the guard doesn't flag
    # them as cross-customer leaks when the response addresses the customer by name.
    for field, label in (("name", "customer's name"), ("email", "customer's email")):
        val = ctx.get(field, "")
        if val:
            ids.setdefault(val, label)
    for order in ctx.get("recent_orders") or []:
        oid = order.get("order_id", "")
        if oid:
            ids.setdefault(oid, "order ID")
    for result in action_results:
        if isinstance(result, dict):
            for v in result.values():
                if isinstance(v, str) and _UUID_RE.match(v):
                    ids.setdefault(v, "")
    for chunk in state.get("retrieved_context") or []:
        for match in _UUID_RE.finditer(chunk.get("chunk_text", "")):
            ids.setdefault(match.group(), "")
    for msg in state.get("messages") or []:
        if msg.get("role") == "customer":
            for match in _UUID_RE.finditer(msg.get("content", "")):
                ids.setdefault(match.group(), "")
    known_ids = ", ".join(
        f"{v} ({label})" if label else v for v, label in sorted(ids.items())
    ) or "(none)"

    # KB content retrieved this turn. No character cap — chunks are bounded to top-5
    # by the knowledge service, and the guard must see all retrieved content to
    # correctly verify claims without false-positive unsupported_claim failures.
    chunks = state.get("retrieved_context") or []
    kb_parts = [c.get("chunk_text", "") for c in chunks]
    kb_content = "\n\n---\n\n".join(kb_parts) or "(none)"

    return OUTPUT_GUARD_PROMPT.format(
        response=response,
        conversation=conversation,
        tools_called=tools_called,
        known_ids=known_ids,
        # Same block the response agent sees, so details it repeats from the
        # order list ("your lamp from Nov 2") are verifiable evidence.
        customer_orders=format_recent_orders(ctx) or "(none)",
        kb_content=kb_content,
    )


async def check_output(
    response: str,
    state: AgentState,
    *,
    bypass: bool = False,
    mock_handoff_intent: bool = False,
) -> dict:
    """Run the output guard and report its verdict to the demo's turn sink
    (a no-op outside the live stream; nothing is reported when bypassed).
    Logic lives in _check_output."""
    result = await _check_output(response, state, bypass=bypass, mock_handoff_intent=mock_handoff_intent)
    if not bypass:
        emit(
            "output_guard",
            verdict="pass" if result.get("safe") else "flagged",
            reason=None if result.get("safe") else result.get("reason"),
        )
        run_trace.record(
            "output_guard",
            verdict="pass" if result.get("safe") else "block",
            failure_type=result.get("reason"),
            explanation=result.get("explanation", ""),
            draft=response[:1500],
        )
        if not result.get("safe"):
            logger.warning("output guard blocked (%s): %s", result.get("reason"), result.get("explanation", ""))
        if result.get("reason") == "guard_error":
            run_trace.fallback("output_guard_error")
    return result


async def _check_output(
    response: str,
    state: AgentState,
    *,
    bypass: bool = False,
    mock_handoff_intent: bool = False,
) -> dict:
    """
    Check the agent's draft response before it reaches the customer.

    Returns:
        {"safe": True, "handoff_intent": bool} if the response passes all checks.
            handoff_intent=True means the guard detected the agent is handing off
            the conversation to a human — caller should fire escalation side effects.
        {"safe": False, "reason": str} if the response should be blocked.
            handoff_intent is not included on the fail path — guard failure is
            handled via _do_escalate() in conversation.py.

    bypass (keyword-only):
        Skip the guard LLM call and return safe=True. Only settable via the
        eval endpoint's disable_output_guard flag (chat.py test mode) — the
        production stream path never sets it, so the guard always runs live.
        Replaces the old APP_ENV=test short-circuit, which made the guard
        untestable through the eval harness.

    mock_handoff_intent (keyword-only):
        Only honoured when bypassing in test mode (APP_ENV=test). When True,
        the returned dict includes handoff_intent=True, exercising the
        downstream path in conversation.py without a real LLM call.

    Fails closed on any LLM error or parse failure.
    """
    if bypass:
        return {
            "safe": True,
            "handoff_intent": mock_handoff_intent and settings.app_env == "test",
        }

    prompt = _build_guard_context(response, state)
    try:
        result = await litellm.acompletion(
            model=settings.litellm_guard_model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            request_timeout=30,
        )
        raw = result.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
        parsed = json.loads(raw.strip())
        # The guard's own explanation ("reason" in its JSON) is kept as
        # `explanation`: before, only failure_type survived, so a block could
        # only be understood by replaying the turn.
        explanation = str(parsed.get("reason") or "")[:1500]
        if parsed.get("verdict") == "pass":
            return {"safe": True, "handoff_intent": bool(parsed.get("handoff_intent", False)),
                    "explanation": explanation}
        return {
            "safe": False,
            "reason": parsed.get("failure_type") or "unknown",
            "explanation": explanation,
        }
    except Exception:
        # Fail closed — never let unvalidated content through on guard error.
        # Reason is preserved so callers can distinguish guard failures from
        # genuine policy violations in escalation logs and eval traces.
        return {"safe": False, "reason": "guard_error"}


async def log_output_guard_blocked(
    db, conversation_id: str, draft_response: str, reason: str, explanation: str = ""
) -> None:
    """Write an audit_logs entry when the output guard blocks a response.

    Only called on the live SSE path (conversation_id non-empty).
    The DB add is unflushed — caller is responsible for committing.
    """
    from backend.db.models import AuditLog  # local import avoids circular deps at module load

    db.add(AuditLog(
        id=str(uuid_mod.uuid4()),
        conversation_id=conversation_id,
        agent_type="output_guard",
        action="output_guard_blocked",
        input_data={"draft_response": draft_response},
        output_data={"failure_reason": reason, "explanation": explanation},
    ))
