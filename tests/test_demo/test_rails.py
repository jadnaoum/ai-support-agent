import asyncio
import uuid

import pytest

from backend.demo.rails import TurnGate, hash_ip
from backend.routers import chat as chat_router


async def test_turn_gate_reports_queue_position_and_limits_concurrency():
    gate = TurnGate(max_concurrent=1)
    assert gate.queue_position() == 0

    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with gate.slot():
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    await entered.wait()
    assert gate.active == 1
    assert gate.queue_position() == 1  # slot taken → next arrival is 1st in line

    async def wait_for_slot():
        async with gate.slot():
            return gate.active

    waiter = asyncio.create_task(wait_for_slot())
    await asyncio.sleep(0.01)
    assert gate.waiting == 1
    assert gate.queue_position() == 2

    release.set()
    await holder
    assert await waiter == 1  # ran only after the holder released
    assert gate.active == 0 and gate.waiting == 0


def test_hash_ip_is_stable_and_not_the_address():
    assert hash_ip("203.0.113.9") == hash_ip("203.0.113.9")
    assert "203.0.113.9" not in hash_ip("203.0.113.9")
    assert hash_ip(None) == hash_ip("unknown")


async def _send(client, conversation, customer, text):
    return await client.post("/api/chat", json={
        "conversation_id": conversation.id, "customer_id": customer.id, "message": text,
    })


async def test_message_length_cap(client, active_conversation, customer, monkeypatch):
    monkeypatch.setattr(chat_router.settings, "demo_max_message_chars", 20)
    ok = await _send(client, active_conversation, customer, "short enough")
    assert ok.status_code == 200
    too_long = await _send(client, active_conversation, customer, "x" * 21)
    assert too_long.status_code == 422
    assert too_long.json()["detail"]["code"] == "message_too_long"


async def test_messages_per_conversation_cap(client, active_conversation, customer, monkeypatch):
    monkeypatch.setattr(chat_router.settings, "demo_max_messages_per_conversation", 2)
    assert (await _send(client, active_conversation, customer, "one")).status_code == 200
    assert (await _send(client, active_conversation, customer, "two")).status_code == 200
    capped = await _send(client, active_conversation, customer, "three")
    assert capped.status_code == 429
    assert capped.json()["detail"]["code"] == "conversation_cap"


async def test_end_conversation_resolves_active(client, db, active_conversation):
    r = await client.post(f"/api/conversations/{active_conversation.id}/end")
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"
    await db.refresh(active_conversation)
    assert active_conversation.status == "resolved"
    assert active_conversation.ended_at is not None


async def test_end_conversation_leaves_escalated_alone(client, db, customer):
    from backend.db.models import Conversation
    conv = Conversation(id=str(uuid.uuid4()), customer_id=customer.id, status="escalated")
    db.add(conv)
    await db.flush()
    r = await client.post(f"/api/conversations/{conv.id}/end")
    assert r.status_code == 200
    assert r.json()["status"] == "escalated"


async def test_end_conversation_unknown_returns_404(client):
    r = await client.post(f"/api/conversations/{uuid.uuid4()}/end")
    assert r.status_code == 404


async def test_send_message_to_closed_conversation_returns_409(client, resolved_conversation, customer):
    r = await _send(client, resolved_conversation, customer, "hello?")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "conversation_closed"
