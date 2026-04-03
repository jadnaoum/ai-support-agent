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
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Escalation, Conversation

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


async def handle_escalation(reason: str, context: dict) -> str:
    """
    Default escalation implementation.

    Logs to the escalations table, marks the conversation as escalated,
    and returns the appropriate handoff message.
    """
    db: AsyncSession | None = context.get("db")
    conversation_id: str = context.get("conversation_id", "")
    confidence: float = context.get("confidence", 0.0)
    messages: list = context.get("messages") or []

    # Use pre-built summary if provided (callers with full state pass it via context);
    # fall back to messages-only summary for callers that don't have the extra fields.
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

    return _HANDOFF_MESSAGE
