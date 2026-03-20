"""
Output guardrail — runs after the conversation agent generates a response,
before the response is sent to the customer.

Rule-based checks (no LLM call — fast and deterministic):

1. Impossible promise detection
   Catches past-tense action claims ("I've cancelled your order", "I've processed
   your refund") when the matching tool was NOT actually called this turn.
   These would be hallucinations — the agent made up that it performed an action.

2. Order ID hallucination detection
   Catches UUID-format strings in the response that did not appear in any of the
   agent's source material (retrieved KB chunks, action results, customer context).

Returns a dict:
  {"safe": True}
  {"safe": False, "reason": str}
"""
import re
from typing import Optional

from backend.agents.state import AgentState

# ---------------------------------------------------------------------------
# Impossible promise detection
# ---------------------------------------------------------------------------

# Maps action keywords in the response → tool name that must appear in actions_taken
_PROMISE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(i'?ve|i have)\s+(cancelled|canceled)\b", re.IGNORECASE), "cancel_order"),
    (re.compile(r"\b(i'?ve|i have)\s+(processed|initiated|submitted)\s+.{0,30}refund", re.IGNORECASE), "process_refund"),
    (re.compile(r"\byour (order|refund) (has been|have been)\s+(cancelled|canceled|processed|initiated)", re.IGNORECASE), "cancel_order"),
    (re.compile(r"\brefund has been (processed|initiated|approved)", re.IGNORECASE), "process_refund"),
]


def _tools_used(state: AgentState) -> set[str]:
    """Return the set of tool names that were actually called this turn."""
    return {
        a.get("action", "")
        for a in (state.get("actions_taken") or [])
    }


def _check_impossible_promises(response: str, state: AgentState) -> Optional[dict]:
    """Return a failed guard result if the response makes an impossible promise."""
    used = _tools_used(state)
    for pattern, required_tool in _PROMISE_PATTERNS:
        if pattern.search(response) and required_tool not in used:
            return {
                "safe": False,
                "reason": "impossible_promise",
            }
    return None


# ---------------------------------------------------------------------------
# Order ID hallucination detection
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _known_ids(state: AgentState) -> set[str]:
    """Collect all UUIDs/IDs that were legitimately present in the agent's context."""
    ids: set[str] = set()

    # Customer context: recent order IDs
    ctx = state.get("customer_context") or {}
    for order in ctx.get("recent_orders") or []:
        oid = order.get("order_id", "")
        if oid:
            ids.add(oid.lower())

    # Action results (tool outputs contain order IDs, refund IDs, etc.)
    for result in state.get("action_results") or []:
        if isinstance(result, dict):
            for v in result.values():
                if isinstance(v, str) and _UUID_RE.match(v):
                    ids.add(v.lower())

    # Retrieved KB chunks — unlikely to contain UUIDs but scan anyway
    for chunk in state.get("retrieved_context") or []:
        text = chunk.get("chunk_text", "")
        for match in _UUID_RE.finditer(text):
            ids.add(match.group().lower())

    # Customer message history — agent may legitimately echo back an ID the
    # customer mentioned
    for msg in state.get("messages") or []:
        if msg.get("role") == "customer":
            for match in _UUID_RE.finditer(msg.get("content", "")):
                ids.add(match.group().lower())

    return ids


def _check_id_hallucination(response: str, state: AgentState) -> Optional[dict]:
    """Return a failed guard result if the response contains a UUID not in the context."""
    known = _known_ids(state)
    for match in _UUID_RE.finditer(response):
        if match.group().lower() not in known:
            return {
                "safe": False,
                "reason": "hallucinated_id",
            }
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_output(response: str, state: AgentState) -> dict:
    """
    Check the agent's response before it is sent to the customer.

    Returns:
        {"safe": True} if the response passes all checks.
        {"safe": False, "reason": str} if any check fails.
    """
    result = _check_impossible_promises(response, state)
    if result:
        return result

    result = _check_id_hallucination(response, state)
    if result:
        return result

    return {"safe": True}
