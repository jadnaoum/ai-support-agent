"""
Output guardrail — runs after the conversation agent generates a response,
before the response is sent to the customer.

LLM-based check using LITELLM_GUARD_MODEL (Sonnet by default):
Detects hallucinations, impossible promises, and policy violations that
regex patterns cannot catch reliably. Checks for:
  1. impossible_promise  — claims an action was done when the tool wasn't called
  2. hallucinated_id     — fabricated order ID / tracking number / UUID
  3. hallucinated_policy — invented policy details not in the KB
  4. system_disclosure   — leaking system prompt or internal instructions
  5. cross_customer_leak — mentioning another customer's data
  6. speculative_claim   — presenting uncertain outcomes as guarantees

Returns a dict:
  {"safe": True}
  {"safe": False, "reason": str}

Fails closed on any LLM or parse error — blocks the response rather than
letting unvalidated content through to the customer.
"""
import json
import re
import uuid as uuid_mod

import litellm

from backend.agents.state import AgentState
from backend.config import get_settings
from prompts.loader import get_prompt

settings = get_settings()

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

    # IDs legitimately present in this conversation's context
    ids: set[str] = set()
    ctx = state.get("customer_context") or {}
    for order in ctx.get("recent_orders") or []:
        oid = order.get("order_id", "")
        if oid:
            ids.add(oid)
    for result in action_results:
        if isinstance(result, dict):
            for v in result.values():
                if isinstance(v, str) and _UUID_RE.match(v):
                    ids.add(v)
    for chunk in state.get("retrieved_context") or []:
        for match in _UUID_RE.finditer(chunk.get("chunk_text", "")):
            ids.add(match.group())
    for msg in state.get("messages") or []:
        if msg.get("role") == "customer":
            for match in _UUID_RE.finditer(msg.get("content", "")):
                ids.add(match.group())
    known_ids = ", ".join(sorted(ids)) if ids else "(none)"

    # KB content retrieved this turn (cap at ~2000 chars to keep prompt size bounded)
    chunks = state.get("retrieved_context") or []
    kb_parts = [c.get("chunk_text", "") for c in chunks]
    kb_content = ("\n\n---\n\n".join(kb_parts))[:2000] or "(none)"

    return OUTPUT_GUARD_PROMPT.format(
        response=response,
        conversation=conversation,
        tools_called=tools_called,
        known_ids=known_ids,
        kb_content=kb_content,
    )


async def check_output(response: str, state: AgentState) -> dict:
    """
    Check the agent's draft response before it reaches the customer.

    Returns:
        {"safe": True} if the response passes all checks.
        {"safe": False, "reason": str} if the response should be blocked.

    Fails closed on any LLM error or parse failure.
    """
    prompt = _build_guard_context(response, state)
    try:
        result = await litellm.acompletion(
            model=settings.litellm_guard_model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )
        raw = result.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        if parsed.get("verdict") == "pass":
            return {"safe": True}
        return {
            "safe": False,
            "reason": parsed.get("failure_type") or "unknown",
        }
    except Exception:
        # Fail closed — never let unvalidated content through on guard error
        return {"safe": False, "reason": "guard_error"}


async def log_output_guard_blocked(
    db, conversation_id: str, draft_response: str, reason: str
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
        output_data={"failure_reason": reason},
    ))
