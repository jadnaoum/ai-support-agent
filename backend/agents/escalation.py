"""
Escalation handler — called directly by the conversation agent when it decides to escalate.

Pluggable interface: async callable(reason: str, context: dict) -> str
Default implementation: handle_escalation

context keys used by the default implementation:
    db:              AsyncSession | None — if None, DB writes are skipped
    conversation_id: str — if empty, DB writes are skipped
    confidence:      float — stored in the escalation record
    messages:        list[dict] — used to build the context summary
    context_summary: str | None — pre-built summary; if present, skips build_context_summary()
    actions_taken:   list[dict] — action history for the context summary
    retrieved_context: list[dict] — KB chunks for the context summary
"""
import logging
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Escalation, Conversation

logger = logging.getLogger(__name__)

# All reason codes that may be passed to run_escalation_side_effects().
# Every call site must pass a value from this set — enforced with a logged
# warning (not a raise) in run_escalation_side_effects, so a drifting label
# is visible in logs without dropping the escalation itself.
#
# Distinct from tools/constants.py::ESCALATION_REASONS, which is a tool-layer
# gate: the set of customer-provided *input* reasons that trigger tool-level
# escalation rejection. The overlap ("defective", "duplicate_item") is intentional
# — these reasons cause both a tool rejection AND a backend escalation record.
ESCALATION_TRIGGER_REASONS: frozenset = frozenset({
    # Tool-signalled: tool returned requires_escalation=True
    "defective",              # defective/damaged item claim
    "duplicate_item",         # duplicate order claim
    "carrier_claim_eligible", # missing package eligible for a carrier claim
    # Output guard failure paths
    "output_guard_block",     # guard failed — specific failure_type is in the
                              #   context summary and the output_guard audit log
    "guard_error",            # guard LLM/parse error (fails closed)
    # Infrastructure failure
    "unhandled_error",        # tool/service error, or repeated system failures (chat.py)
    # Intent-classifier-triggered
    "unable_to_clarify",      # clarification cap hit or clarification blocked
    "customer_requested",     # explicit ask for a human (classifier trigger "requested" or absent)
    "threat",                 # legal/chargeback/regulatory threat tied to a demand
                              #   (classifier trigger "threat" on escalation_request)
    "classifier_decided",     # classifier judged a human is needed for any other
                              #   reason (trigger "decided"), e.g. the person
                              #   writing is not the account holder — the
                              #   handoff summary records the specifics
    "abusive_input",          # input guard flagged abusive language
    "harmful_input",          # input guard flagged talk of harming someone or
                              #   breaking the law (immediate, like abusive)
    "repeated_blocks",        # 3 consecutive input-guard blocks
    "provocative_input",      # 2nd input-guard "provocative" block this conversation
                              #   (the 1st gets a redirect with a warning)
    "excessive_small_talk",   # small_talk intent: 3rd in a row or 5th in total
    "customer_frustrated",    # input guard emotion: extreme_negative (at once) or
                              #   high_negative on 2 turns in a row (PENDING #37)
                              #   (2nd in a row / 4th in total get a warning)
    # Output-guard handoff detection (Option 2) — split structurally by whether
    # the KB search this turn actually found anything (retrieved_context state):
    "no_information",         # agent handed off after the KB came back empty
    "llm_escalation_intent",  # agent handed off with KB content present
                              #   (needs approval / human process / judgment call)
})

_HANDOFF_MESSAGE = "This one needs a human — let me connect you with someone who can help."


def build_context_summary(
    messages: list[dict],
    actions_taken: list[dict] = None,
    retrieved_context: list[dict] = None,
    reason: str = "",
) -> str:
    """
    Build a structured, deterministic summary for the human agent picking up the conversation.

    Format:
        Customer issue: <first substantive customer message>
        Orders: #ORD-1, #ORD-2
        Actions attempted: track_order(#ORD-1) → success | cancel_order(#ORD-1) → shipped
        KB retrieved: Returns and Refunds Policy
        Escalation reason: customer_requested
    """
    lines = []

    # Customer issue — first customer message longer than 20 chars, else first customer message
    customer_msgs = [m["content"] for m in messages if m.get("role") == "customer"]
    issue = next((m for m in customer_msgs if len(m) > 20), customer_msgs[0] if customer_msgs else None)
    lines.append(f"Customer issue: {issue}" if issue else "Customer issue: No messages recorded.")

    # Orders — deduplicated order IDs from action_service entries
    action_entries = [
        e for e in (actions_taken or [])
        if e.get("service") == "action_service" and e.get("order_id")
    ]
    seen_orders: list = []
    for e in action_entries:
        oid = e["order_id"]
        if oid not in seen_orders:
            seen_orders.append(oid)
    if seen_orders:
        lines.append("Orders: " + ", ".join(f"#{o}" for o in seen_orders))

    # Actions attempted — tool(#order_id) → result_detail
    action_parts = []
    for e in action_entries:
        oid = f"#{e['order_id']}" if e.get("order_id") else ""
        detail = e.get("result_detail") or ("success" if e.get("success") else "failed")
        action_parts.append(f"{e['action']}({oid}) → {detail}")
    if action_parts:
        lines.append("Actions attempted: " + " | ".join(action_parts))

    # KB retrieved — deduplicated article titles
    titles: list = []
    for chunk in (retrieved_context or []):
        t = chunk.get("title")
        if t and t not in titles:
            titles.append(t)
    if titles:
        lines.append("KB retrieved: " + ", ".join(titles))

    # Escalation reason
    if reason:
        lines.append(f"Escalation reason: {reason}")

    return "\n".join(lines)


async def run_escalation_side_effects(reason: str, context: dict) -> str:
    """
    Write the escalation record and mark the conversation as escalated.
    Returns the context_summary stored in the record.

    Separated from handle_escalation so callers that generate their own
    customer-facing response (e.g. tool-signalled defective item escalations
    where the LLM produces a KB-rich explanation) can fire the backend side
    effects without overwriting the response with the canned handoff message.
    """
    db: "AsyncSession | None" = context.get("db")
    conversation_id: str = context.get("conversation_id", "")
    confidence: float = context.get("confidence", 0.0)
    messages: list = context.get("messages") or []

    # Vocabulary enforcement: the reason column is what human triage and the
    # admin dashboard filter by. An off-list value is stored as-is (never drop
    # data silently) but logged loudly so the drift is fixed at the call site.
    if reason not in ESCALATION_TRIGGER_REASONS:
        logger.warning(
            "Escalation reason %r is not in ESCALATION_TRIGGER_REASONS — "
            "add it to the set or fix the call site", reason,
        )

    context_summary = context.get("context_summary") or build_context_summary(messages)

    if db and conversation_id:
        db.add(Escalation(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            reason=reason,
            agent_confidence=confidence,
            context_summary=context_summary,
        ))
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if conversation:
            conversation.status = "escalated"
        await db.commit()

    return context_summary


async def handle_escalation(reason: str, context: dict) -> str:
    """
    Default escalation implementation.

    Logs to the escalations table, marks the conversation as escalated,
    and returns the appropriate handoff message.
    """
    await run_escalation_side_effects(reason, context)
    return _HANDOFF_MESSAGE
