"""Stream event order + turn persistence + timeline endpoint (graph mocked)."""
import json
import uuid
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy import select

from backend.db.models import Turn, Message, AuditLog


def _parse_sse(text: str) -> list:
    """[(event, data)] from a raw SSE body."""
    out = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        event, data = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if event:
            out.append((event, "\n".join(data)))
    return out


KB_OUTPUT = {
    "response": "Electronics have a 14-day return window.",
    "confidence": 0.9,
    "actions_taken": [
        {"service": "knowledge_service", "action": "search_kb", "query": "return window",
         "chunks_retrieved": 2, "top_similarity": 0.8},
    ],
    "retrieved_context": [{"title": "Electronics Return Window", "similarity": 0.8}],
    "consecutive_blocks": 0,
}

ESCALATION_OUTPUT = {
    "response": "This one needs a human.",
    "confidence": 1.0,
    "requires_escalation": True,
    "escalation_reason": "abusive_input",
    "context_summary": "Customer was abusive.",
    "actions_taken": [{"service": "escalation_handler", "action": "escalate", "reason": "abusive_input"}],
    "consecutive_blocks": 0,
}


@pytest.fixture
async def demo_conversation(client, catalog):
    created = (await client.post("/api/demo/customer", json={"name": "Galadriel", "scenario_id": "missed-window"})).json()
    conv = (await client.post("/api/conversations", json={"customer_id": created["customer_id"]})).json()
    return created, conv["conversation_id"]


async def _send(client, customer_id, conversation_id, text):
    r = await client.post("/api/chat", json={"conversation_id": conversation_id, "customer_id": customer_id, "message": text})
    assert r.status_code == 200, r.text


@patch("backend.agents.graph.graph.ainvoke", new_callable=AsyncMock, return_value=KB_OUTPUT)
async def test_stream_event_order_and_turn_persisted(mock_graph, client, db, demo_conversation):
    created, conv_id = demo_conversation
    await _send(client, created["customer_id"], conv_id, "What's the return window on my laptop?")

    r = await client.get(f"/api/chat/stream/{conv_id}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    names = [e for e, _ in events]
    assert names[0] == "turn"                       # pipeline steps before any token
    assert names.index("turn") < names.index("token")
    assert names[-2:] == ["turn_complete", "done"]
    assert "escalated" not in names

    turn = json.loads(events[0][1])
    assert turn["index"] == 1
    assert [e["type"] for e in turn["events"]] == ["input_guard", "kb_retrieval", "output_guard"]
    assert turn["events"][1]["articles"] == ["Electronics Return Window"]
    assert turn["latency_ms"] >= 0
    assert turn["cost_basis"] == "unavailable"     # graph mocked → no LLM calls
    assert turn["llm_calls"] == 0

    rows = (await db.execute(select(Turn).where(Turn.conversation_id == conv_id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].index == 1 and rows[0].events[1]["type"] == "kb_retrieval"
    assert rows[0].customer_message_id is not None and rows[0].agent_message_id is not None


@patch("backend.agents.graph.graph.ainvoke", new_callable=AsyncMock, return_value=ESCALATION_OUTPUT)
async def test_stream_escalation_turn(mock_graph, client, db, demo_conversation):
    created, conv_id = demo_conversation
    await _send(client, created["customer_id"], conv_id, "you are useless")
    # simulate the guard's audit row being written during the turn
    db.add(AuditLog(id=str(uuid.uuid4()), conversation_id=conv_id, agent_type="input_guard",
                    action="input_guard_blocked", input_data={}, output_data={"category": "abusive"}))
    await db.commit()
    # …but rows that pre-exist the turn must be ignored → write it inside the mocked graph instead
    async def graph_with_audit(*args, **kwargs):
        db.add(AuditLog(id=str(uuid.uuid4()), conversation_id=conv_id, agent_type="input_guard",
                        action="input_guard_blocked", input_data={}, output_data={"category": "abusive"}))
        await db.commit()
        return ESCALATION_OUTPUT
    mock_graph.side_effect = graph_with_audit

    r = await client.get(f"/api/chat/stream/{conv_id}")
    events = _parse_sse(r.text)
    names = [e for e, _ in events]
    assert "escalated" in names and names[-1] == "done"
    turn = json.loads(dict(events)["turn_complete"])
    assert [e["type"] for e in turn["events"]] == ["input_guard", "escalation"]
    assert turn["events"][0]["reason"] == "abusive"
    assert turn["requires_escalation"] is True and turn["escalation_reason"] == "abusive_input"


@patch("backend.agents.graph.graph.ainvoke", new_callable=AsyncMock, side_effect=RuntimeError("boom"))
async def test_stream_graph_error_persists_error_turn(mock_graph, client, db, demo_conversation):
    created, conv_id = demo_conversation
    await _send(client, created["customer_id"], conv_id, "hello")
    r = await client.get(f"/api/chat/stream/{conv_id}")
    events = _parse_sse(r.text)
    names = [e for e, _ in events]
    assert names[0] == "turn" and names[-1] == "done"
    turn = json.loads(events[0][1])
    assert turn["error"] == "graph_error"
    assert turn["events"][0]["type"] == "error"
    rows = (await db.execute(select(Turn).where(Turn.conversation_id == conv_id))).scalars().all()
    assert len(rows) == 1 and rows[0].error == "graph_error"


@patch("backend.agents.graph.graph.ainvoke", new_callable=AsyncMock, return_value=KB_OUTPUT)
async def test_timeline_endpoint(mock_graph, client, demo_conversation):
    created, conv_id = demo_conversation
    await _send(client, created["customer_id"], conv_id, "Return window?")
    await client.get(f"/api/chat/stream/{conv_id}")
    await _send(client, created["customer_id"], conv_id, "Thanks!")
    await client.get(f"/api/chat/stream/{conv_id}")

    r = await client.get(f"/api/conversations/{conv_id}/timeline")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conversation"]["id"] == conv_id and body["conversation"]["status"] == "active"
    assert body["customer"]["name"] == "Galadriel" and body["customer"]["orders"]
    assert [m["role"] for m in body["messages"]] == ["customer", "agent", "customer", "agent"]
    assert [t["index"] for t in body["turns"]] == [1, 2]
    assert body["turns"][0]["customer_message"] == "Return window?"
    assert body["turns"][0]["response"] == KB_OUTPUT["response"]
    assert body["totals"]["messages"] == 2 and body["totals"]["turns"] == 2
    assert body["totals"]["cost_basis"] == "unavailable"
    ids = [c["id"] for c in body["checks"]]
    assert ids == ["inputs_screened", "responses_verified", "confirmation_before_change", "policy_grounded", "no_pii"]
    assert all(c["status"] in ("pass", "na") for c in body["checks"])

    # end + rate → timeline shows csat
    await client.post(f"/api/conversations/{conv_id}/end")
    await client.post("/api/csat", json={"conversation_id": conv_id, "rating": 5, "tags": ["quick_and_easy"]})
    body = (await client.get(f"/api/conversations/{conv_id}/timeline")).json()
    assert body["conversation"]["status"] == "resolved"
    assert body["conversation"]["csat"] == {"rating": 5, "tags": ["quick_and_easy"], "free_text": None}


async def test_timeline_unknown_conversation(client):
    assert (await client.get(f"/api/conversations/{uuid.uuid4()}/timeline")).status_code == 404


@patch("backend.agents.graph.graph.ainvoke", new_callable=AsyncMock, return_value=KB_OUTPUT)
async def test_queued_event_when_gate_is_full(mock_graph, client, demo_conversation, monkeypatch):
    from backend.routers import chat as chat_router
    created, conv_id = demo_conversation
    await _send(client, created["customer_id"], conv_id, "hi")
    gate = chat_router._turn_gate
    gate._semaphore()
    monkeypatch.setattr(gate, "active", gate.max_concurrent)  # pretend every slot is busy
    try:
        r = await client.get(f"/api/chat/stream/{conv_id}")
    finally:
        gate.active = 0
    names = [e for e, _ in _parse_sse(r.text)]
    assert names[0] == "queued"
