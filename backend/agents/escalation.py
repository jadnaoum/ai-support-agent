"""
Escalation handler — triggered by the conversation agent when it decides to escalate.

Responsibilities:
- Log the escalation reason and context to the escalations table
- Mark the conversation status as "escalated"
- Set a friendly handoff response message

Routes directly to END. The response is delivered by the SSE endpoint.
"""
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import AgentState
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


def _build_context_summary(messages: list[dict]) -> str:
    """Summarise the last few customer messages as plain text for the escalation log."""
    customer_msgs = [m["content"] for m in messages if m.get("role") == "customer"]
    recent = customer_msgs[-3:]
    return " | ".join(recent) if recent else "No messages recorded."


async def escalation_handler_node(state: AgentState, config: dict) -> dict:
    """LangGraph node: log escalation → set handoff response → END."""
    db: AsyncSession = config["configurable"]["db"]
    conversation_id: str = config["configurable"].get("conversation_id", "")

    reason = state.get("escalation_reason") or "customer_requested"
    confidence = state.get("confidence", 0.0)
    context_summary = _build_context_summary(state.get("messages") or [])

    if conversation_id:
        # Write escalation record
        db.add(Escalation(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            reason=reason,
            agent_confidence=confidence,
            context_summary=context_summary,
        ))

        # Mark conversation as escalated
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if conversation:
            conversation.status = "escalated"

        await db.commit()

    handoff_message = _HANDOFF_MESSAGES.get(reason, _HANDOFF_DEFAULT)

    return {
        "response": handoff_message,
        "requires_escalation": True,
        "pending_service": "",
        "context_summary": context_summary,
        "actions_taken": (state.get("actions_taken") or []) + [
            {
                "service": "escalation_handler",
                "action": "escalate",
                "reason": reason,
            }
        ],
    }
