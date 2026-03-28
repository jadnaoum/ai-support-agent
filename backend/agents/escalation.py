"""
Escalation handler — called directly by the conversation agent when it decides to escalate.

Pluggable interface: async callable(reason: str, context: dict) -> str
Default implementation: handle_escalation

context keys used by the default implementation:
    db:              AsyncSession | None — if None, DB writes are skipped
    conversation_id: str — if empty, DB writes are skipped
    confidence:      float — stored in the escalation record
    messages:        list[dict] — used to build the context summary
"""
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Escalation, Conversation

# HANDOFF MESSAGES — one per escalation reason; edit here to tune tone
_HANDOFF_MESSAGES = {
    "customer_requested": (
        "Of course — I'm connecting you with a human agent right away. "
        "Please hold on and someone from our support team will be with you shortly."
    ),
    "low_confidence": (
        "I want to make sure you get the best possible help with this. "
        "I'm transferring you to a specialist who can assist you further. "
        "Please hold on."
    ),
    "repeated_failure": (
        "I'm sorry I haven't been able to resolve this for you. "
        "I'm connecting you with a member of our support team now. "
        "Please hold on."
    ),
    "policy_exception": (
        "This request needs a review by our support team. "
        "I'm connecting you with a specialist who can help."
    ),
    "unable_to_clarify": (
        "I wasn't able to get enough detail to handle this for you, "
        "so I'm connecting you with a human agent who can ask the right questions and help you directly. "
        "Please hold on."
    ),
    "repeated_blocks": (
        "It looks like we've been going in circles and I haven't been able to help. "
        "Let me connect you with a member of our support team who can assist you directly. "
        "Please hold on."
    ),
}
_HANDOFF_DEFAULT = (
    "I'm transferring you to a human agent who can better assist you. "
    "Please hold on."
)


def build_context_summary(messages: list[dict]) -> str:
    """Summarise the last few customer messages as plain text for the escalation log."""
    customer_msgs = [m["content"] for m in messages if m.get("role") == "customer"]
    recent = customer_msgs[-3:]
    return " | ".join(recent) if recent else "No messages recorded."


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

    context_summary = build_context_summary(messages)

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

    return _HANDOFF_MESSAGES.get(reason, _HANDOFF_DEFAULT)
