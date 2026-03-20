"""
Tests for the escalation handler node.
Uses real test DB to verify escalation records and conversation status updates.
"""
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.escalation import escalation_handler_node
from backend.db.models import Customer, Conversation, Escalation


def make_state(reason: str = "customer_requested", **overrides) -> dict:
    base = {
        "messages": [
            {"role": "customer", "content": "I need help with my order."},
            {"role": "agent", "content": "Sure, how can I help?"},
            {"role": "customer", "content": "I want to speak to a human."},
        ],
        "customer_id": str(uuid.uuid4()),
        "customer_context": {},
        "retrieved_context": [],
        "action_results": [],
        "confidence": 0.5,
        "requires_escalation": True,
        "escalation_reason": reason,
        "actions_taken": [],
        "response": "",
        "pending_service": "escalation",
        "pending_action": {},
    }
    base.update(overrides)
    return base


@pytest.fixture
async def active_conv(db: AsyncSession):
    c = Customer(id=str(uuid.uuid4()), name="Test", email=f"esc-{uuid.uuid4()}@test.com")
    db.add(c)
    await db.flush()
    conv = Conversation(id=str(uuid.uuid4()), customer_id=c.id, status="active")
    db.add(conv)
    await db.commit()
    return conv


# ---------------------------------------------------------------------------
# Response and state
# ---------------------------------------------------------------------------

async def test_returns_handoff_response(db):
    result = await escalation_handler_node(
        make_state("customer_requested"),
        {"configurable": {"db": db}},
    )
    assert isinstance(result["response"], str)
    assert len(result["response"]) > 0


async def test_sets_requires_escalation(db):
    result = await escalation_handler_node(
        make_state(),
        {"configurable": {"db": db}},
    )
    assert result["requires_escalation"] is True


async def test_clears_pending_service(db):
    result = await escalation_handler_node(
        make_state(),
        {"configurable": {"db": db}},
    )
    assert result["pending_service"] == ""


async def test_appends_to_actions_taken(db):
    result = await escalation_handler_node(
        make_state(),
        {"configurable": {"db": db}},
    )
    assert len(result["actions_taken"]) == 1
    assert result["actions_taken"][0]["service"] == "escalation_handler"
    assert result["actions_taken"][0]["action"] == "escalate"


async def test_different_reasons_produce_different_messages(db):
    r1 = await escalation_handler_node(
        make_state("customer_requested"), {"configurable": {"db": db}}
    )
    r2 = await escalation_handler_node(
        make_state("low_confidence"), {"configurable": {"db": db}}
    )
    assert r1["response"] != r2["response"]


async def test_unknown_reason_uses_default_message(db):
    result = await escalation_handler_node(
        make_state("something_new"),
        {"configurable": {"db": db}},
    )
    assert len(result["response"]) > 0


# ---------------------------------------------------------------------------
# DB writes (require conversation_id in config)
# ---------------------------------------------------------------------------

async def test_writes_escalation_record(db, active_conv):
    await escalation_handler_node(
        make_state("customer_requested"),
        {"configurable": {"db": db, "conversation_id": active_conv.id}},
    )
    result = await db.execute(
        select(Escalation).where(Escalation.conversation_id == active_conv.id)
    )
    escalations = result.scalars().all()
    assert len(escalations) == 1
    assert escalations[0].reason == "customer_requested"


async def test_escalation_record_stores_confidence(db, active_conv):
    await escalation_handler_node(
        make_state("low_confidence", confidence=0.42),
        {"configurable": {"db": db, "conversation_id": active_conv.id}},
    )
    result = await db.execute(
        select(Escalation).where(Escalation.conversation_id == active_conv.id)
    )
    record = result.scalar_one()
    assert abs(record.agent_confidence - 0.42) < 0.01


async def test_marks_conversation_as_escalated(db, active_conv):
    await escalation_handler_node(
        make_state(),
        {"configurable": {"db": db, "conversation_id": active_conv.id}},
    )
    await db.refresh(active_conv)
    assert active_conv.status == "escalated"


async def test_no_conversation_id_does_not_crash(db):
    """Handler must work even when conversation_id is not in config (e.g. tests)."""
    result = await escalation_handler_node(
        make_state(),
        {"configurable": {"db": db}},
    )
    assert result["response"]
