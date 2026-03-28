"""
Tests for the escalation handler.
Uses real test DB to verify escalation records and conversation status updates.
"""
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.escalation import handle_escalation
from backend.db.models import Customer, Conversation, Escalation


def make_context(reason: str = "customer_requested", **overrides) -> dict:
    base = {
        "db": None,
        "conversation_id": "",
        "confidence": 0.5,
        "messages": [
            {"role": "customer", "content": "I need help with my order."},
            {"role": "agent", "content": "Sure, how can I help?"},
            {"role": "customer", "content": "I want to speak to a human."},
        ],
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
# Return value
# ---------------------------------------------------------------------------

async def test_returns_handoff_string(db):
    result = await handle_escalation("customer_requested", make_context(db=db))
    assert isinstance(result, str)
    assert len(result) > 0


async def test_different_reasons_produce_different_messages(db):
    m1 = await handle_escalation("customer_requested", make_context(db=db))
    m2 = await handle_escalation("low_confidence", make_context(db=db))
    assert m1 != m2


async def test_unknown_reason_uses_default_message(db):
    result = await handle_escalation("something_new", make_context(db=db))
    assert isinstance(result, str)
    assert len(result) > 0


async def test_all_defined_reasons_return_non_empty(db):
    reasons = [
        "customer_requested", "low_confidence", "repeated_failure",
        "policy_exception", "unable_to_clarify", "repeated_blocks",
    ]
    for reason in reasons:
        msg = await handle_escalation(reason, make_context(db=db))
        assert len(msg) > 0, f"Empty message for reason: {reason}"


# ---------------------------------------------------------------------------
# DB writes (require conversation_id in context)
# ---------------------------------------------------------------------------

async def test_writes_escalation_record(db, active_conv):
    await handle_escalation(
        "customer_requested",
        make_context(db=db, conversation_id=active_conv.id),
    )
    result = await db.execute(
        select(Escalation).where(Escalation.conversation_id == active_conv.id)
    )
    escalations = result.scalars().all()
    assert len(escalations) == 1
    assert escalations[0].reason == "customer_requested"


async def test_escalation_record_stores_confidence(db, active_conv):
    await handle_escalation(
        "low_confidence",
        make_context(db=db, conversation_id=active_conv.id, confidence=0.42),
    )
    result = await db.execute(
        select(Escalation).where(Escalation.conversation_id == active_conv.id)
    )
    record = result.scalar_one()
    assert abs(record.agent_confidence - 0.42) < 0.01


async def test_marks_conversation_as_escalated(db, active_conv):
    await handle_escalation(
        "customer_requested",
        make_context(db=db, conversation_id=active_conv.id),
    )
    await db.refresh(active_conv)
    assert active_conv.status == "escalated"


async def test_no_conversation_id_does_not_crash(db):
    """Handler must work even when conversation_id is empty (e.g. test mode)."""
    result = await handle_escalation("customer_requested", make_context(db=db))
    assert isinstance(result, str)


async def test_no_db_does_not_crash():
    """Handler must work with no DB at all."""
    result = await handle_escalation("low_confidence", make_context(db=None))
    assert isinstance(result, str)
