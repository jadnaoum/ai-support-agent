import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from backend.config import get_settings
from backend.db.session import get_db
from backend.db.models import Conversation, Message, Customer, AuditLog, Turn
from backend.agents.state import AgentState
from backend.tools.customer_tools import get_customer_context
from backend.demo.rails import TurnGate
from backend.demo import usage as turn_usage
from backend.demo import sink as turn_sink
from backend.demo.events import derive_events, error_events, turn_payload, merge_live

logger = logging.getLogger(__name__)

settings = get_settings()
router = APIRouter(prefix="/api", tags=["chat"])

# Consecutive infrastructure failures (graph timeout/exception) in one
# conversation before the turn escalates to a human instead of apologising again.
_FAILURE_ESCALATION_THRESHOLD = 3

_ESCALATED_NOTICE = (
    "This conversation is with our support team now — a specialist will follow up with you directly."
)

# Global cap on concurrently running agent turns (public-demo rail).
_turn_gate = TurnGate(settings.demo_max_concurrent_turns)


def _sse(event: str, payload) -> ServerSentEvent:
    """JSON-bodied SSE event."""
    return ServerSentEvent(data=json.dumps(payload), event=event)


async def _persist_turn(db: AsyncSession, conversation_id: str, payload: dict,
                        customer_message_id: "str | None", agent_message_id: "str | None",
                        started_at: datetime) -> None:
    """Store the turn record (see backend/demo/events.turn_payload) so the
    timeline can be replayed. Best-effort: never fails the turn."""
    try:
        db.add(Turn(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            index=payload["index"],
            customer_message_id=customer_message_id,
            agent_message_id=agent_message_id,
            started_at=started_at,
            latency_ms=payload.get("latency_ms"),
            llm_calls=payload.get("llm_calls"),
            prompt_tokens=payload.get("prompt_tokens"),
            completion_tokens=payload.get("completion_tokens"),
            cost_usd=payload.get("cost_usd"),
            cost_basis=payload.get("cost_basis") or "unavailable",
            events=payload.get("events") or [],
            escalation_reason=payload.get("escalation_reason"),
            context_summary=payload.get("context_summary"),
            error=payload.get("error"),
        ))
        await db.commit()
    except Exception:
        logger.exception("Failed to persist turn for conversation %s", conversation_id)
        await db.rollback()


async def _record_turn_failure(db: AsyncSession, conversation_id: str, kind: str) -> bool:
    """Persist an audit row for an infrastructure failure and escalate after
    _FAILURE_ESCALATION_THRESHOLD consecutive failures.

    Without this, a customer facing a degraded system gets an apology every
    turn with no path to a human, and nothing is persisted anywhere.
    Returns True if the conversation was escalated.
    """
    try:
        await db.rollback()  # the failed turn may have left the session dirty
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return False
        turn_state = dict(conversation.turn_state or {})
        failures = int(turn_state.get("consecutive_failures", 0)) + 1
        turn_state["consecutive_failures"] = failures

        db.add(AuditLog(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            agent_type="infrastructure",
            action=kind,
            input_data={},
            output_data={"consecutive_failures": failures},
        ))

        escalated = False
        if failures >= _FAILURE_ESCALATION_THRESHOLD:
            from backend.agents.escalation import run_escalation_side_effects  # noqa: PLC0415
            await run_escalation_side_effects("unhandled_error", {
                "db": db,
                "conversation_id": conversation_id,
                "confidence": 0.0,
                "messages": [],
                "context_summary": (
                    f"Escalated after {failures} consecutive system failures ({kind}). "
                    "The customer has not received an agent response for their last messages."
                ),
            })
            escalated = True

        await db.execute(
            sa_update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(turn_state=None if escalated else turn_state)
        )
        await db.commit()
        return escalated
    except Exception:
        logger.exception("Failed to record turn failure for conversation %s", conversation_id)
        return False


class NewConversationRequest(BaseModel):
    customer_id: str


class NewConversationResponse(BaseModel):
    conversation_id: str


class SendMessageRequest(BaseModel):
    conversation_id: str
    customer_id: str
    message: str


class SendMessageResponse(BaseModel):
    conversation_id: str
    message_id: str


@router.post("/conversations", response_model=NewConversationResponse)
async def create_conversation(
    body: NewConversationRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Customer).where(Customer.id == body.customer_id)
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    conversation = Conversation(
        id=str(uuid.uuid4()),
        customer_id=body.customer_id,
        status="active",
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return NewConversationResponse(conversation_id=conversation.id)


class EndConversationResponse(BaseModel):
    conversation_id: str
    status: str


@router.post("/conversations/{conversation_id}/end", response_model=EndConversationResponse)
async def end_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Visitor ended the chat. Active → resolved (so CSAT is accepted);
    escalated/resolved conversations are left as they are."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.status == "active":
        conversation.status = "resolved"
        conversation.ended_at = datetime.now(timezone.utc)
        await db.commit()
    return EndConversationResponse(conversation_id=conversation.id, status=conversation.status)


@router.post("/chat", response_model=SendMessageResponse)
async def send_message(
    body: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Conversation).where(Conversation.id == body.conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.customer_id != body.customer_id:
        raise HTTPException(status_code=403, detail="Customer does not own this conversation")
    if conversation.status != "active":
        raise HTTPException(status_code=409, detail={"code": "conversation_closed", "status": conversation.status})

    # Public-demo rails: bound message length and turns per conversation so a
    # single visitor cannot burn the shared rate limit (see backend/config.py).
    if len(body.message) > settings.demo_max_message_chars:
        raise HTTPException(
            status_code=422,
            detail={"code": "message_too_long", "max_chars": settings.demo_max_message_chars},
        )
    sent = await db.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id == body.conversation_id, Message.role == "customer",
        )
    )
    if int(sent.scalar() or 0) >= settings.demo_max_messages_per_conversation:
        raise HTTPException(
            status_code=429,
            detail={"code": "conversation_cap", "max_messages": settings.demo_max_messages_per_conversation},
        )

    message = Message(
        id=str(uuid.uuid4()),
        conversation_id=body.conversation_id,
        role="customer",
        content=body.message,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    return SendMessageResponse(
        conversation_id=body.conversation_id,
        message_id=message.id,
    )


@router.get("/chat/stream/{conversation_id}")
async def stream_response(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """SSE endpoint — streams agent response tokens, then persists Message + AuditLog."""
    # 1. Validate conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # 1a. Escalated conversations no longer run the agent. A human owns the
    # conversation from here — running the graph again would let the bot act
    # (with full write-tool access) on a case a specialist may be working.
    # The "escalated" event tells the frontend to disable the input.
    if conversation.status == "escalated":
        async def escalated_generator():
            yield ServerSentEvent(data=_ESCALATED_NOTICE, event="token")
            yield ServerSentEvent(data="", event="escalated")
            yield ServerSentEvent(data="", event="done")
        return EventSourceResponse(escalated_generator())

    # 2. Load the latest customer message (required to run the agent)
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "customer")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    latest_msg = result.scalar_one_or_none()
    if not latest_msg:
        raise HTTPException(status_code=422, detail="No customer message found in conversation")
    # Plain values: a rollback on the failure path expires ORM instances, and
    # touching an expired attribute inside the generator would try lazy IO.
    latest_msg_id = latest_msg.id
    latest_msg_content = latest_msg.content

    # 3. Load conversation history for agent state — the most recent
    # max_context_messages, in chronological order. (Ascending + limit would
    # keep the OLDEST window: past 50 messages the agent would never see the
    # customer's actual latest message, and the input guard would screen a
    # stale one instead.)
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(settings.max_context_messages)
    )
    all_messages = list(reversed(result.scalars().all()))
    messages_for_state = [
        {"role": m.role, "content": m.content}
        for m in all_messages
        if m.role in ("customer", "agent")
    ]

    # 4. Load customer context (best-effort — never block the request on failure)
    try:
        ctx_result = await get_customer_context(db, str(conversation.customer_id))
        customer_context = ctx_result if ctx_result.get("success") else {}
    except Exception:
        customer_context = {}

    # 5. Build initial agent state
    # 5a. Load persisted turn state before building initial_state
    prior_turn_state = conversation.turn_state or {}

    initial_state: AgentState = {
        "messages": messages_for_state,
        "customer_id": str(conversation.customer_id),
        "customer_context": customer_context,
        "retrieved_context": [],
        "action_results": [],
        "confidence": 0.0,
        "requires_escalation": False,
        "escalation_reason": "",
        "actions_taken": [],        # always empty at turn start — service_ran gate depends on this
        "prior_turn_actions": prior_turn_state.get("actions_taken", []),  # cross-turn confirmation gate
        "response": "",
        "pending_service": "",
        "pending_action": {},
        "inferred_intent": "",
        "last_clarification_source": prior_turn_state.get("last_clarification_source", ""),
        "context_summary": "",
        "consecutive_blocks": prior_turn_state.get("consecutive_blocks", 0),
        "consecutive_small_talk": prior_turn_state.get("consecutive_small_talk", 0),
        "total_small_talk": prior_turn_state.get("total_small_talk", 0),
        "provocative_count": prior_turn_state.get("provocative_count", 0),
        "consecutive_high_negative": prior_turn_state.get("consecutive_high_negative", 0),
        "service_call_count": 0,
        "classifier_reasoning": "",
    }

    # 5b. Turn bookkeeping: index, and the audit rows that already exist so the
    # ones the guards write during THIS turn can be told apart afterwards.
    turn_count = await db.execute(
        select(func.count(Turn.id)).where(Turn.conversation_id == conversation_id)
    )
    turn_index = int(turn_count.scalar() or 0) + 1
    prior_audit = await db.execute(
        select(AuditLog.id).where(AuditLog.conversation_id == conversation_id)
    )
    prior_audit_ids = {row[0] for row in prior_audit.all()}
    turn_started_at = datetime.now(timezone.utc)

    # Lazy import — avoids LangGraph compile() running at module load time,
    # which conflicts with pytest-asyncio's event loop.
    from backend.agents.graph import graph  # noqa: PLC0415

    # 6. SSE event generator
    #
    # Event order: [queued] → step… → turn (final step list) → token… →
    # turn_complete → [escalated] → done. Steps are emitted from inside the
    # pipeline (guards, service nodes) and forwarded while the graph runs; the
    # `turn` event carries the reconciled list (live steps + the escalation
    # record derived from the final output). Response text is still the
    # finished response split on spaces (PENDING #18).
    async def event_generator():
        position = _turn_gate.queue_position()
        if position:
            yield _sse("queued", {"position": position})

        async with _turn_gate.slot():
            usage = turn_usage.TurnUsage()
            usage_token = turn_usage.start(usage)
            sink = turn_sink.TurnSink()
            sink_token = turn_sink.start(sink)
            t0 = time.monotonic()
            try:
                config = {"configurable": {"db": db, "conversation_id": conversation_id}}
                loop = asyncio.get_running_loop()
                graph_task = asyncio.ensure_future(graph.ainvoke(initial_state, config=config))
                deadline = loop.time() + 90
                try:
                    while not graph_task.done():
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            graph_task.cancel()
                            raise asyncio.TimeoutError
                        getter = asyncio.ensure_future(sink.queue.get())
                        done, _ = await asyncio.wait(
                            {graph_task, getter}, timeout=min(remaining, 1.0),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if getter in done:
                            yield _sse("step", {"index": turn_index, **getter.result()})
                        else:
                            getter.cancel()
                    while not sink.queue.empty():
                        yield _sse("step", {"index": turn_index, **sink.queue.get_nowait()})
                    final_output = graph_task.result()
                except asyncio.TimeoutError:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    escalated = await _record_turn_failure(db, conversation_id, "graph_timeout")
                    payload = turn_payload(
                        turn_index,
                        error_events("graph_timeout", "The agent took too long to respond."),
                        latency_ms, usage.as_dict(),
                        {"requires_escalation": escalated,
                         "escalation_reason": "unhandled_error" if escalated else ""},
                        error="graph_timeout",
                    )
                    await _persist_turn(db, conversation_id, payload, latest_msg_id, None, turn_started_at)
                    yield _sse("turn", payload)
                    if escalated:
                        yield ServerSentEvent(
                            data="I'm having repeated trouble on my end, so I've passed this to a specialist who will follow up with you.",
                            event="token",
                        )
                        yield _sse("turn_complete", payload)
                        yield ServerSentEvent(data="", event="escalated")
                    else:
                        yield ServerSentEvent(
                            data="Something went wrong on our end — please try again in a moment.",
                            event="token",
                        )
                        yield _sse("turn_complete", payload)
                    yield ServerSentEvent(data="", event="done")
                    return
                finally:
                    turn_usage.stop(usage_token)
                    turn_sink.stop(sink_token)

                latency_ms = int((time.monotonic() - t0) * 1000)
                await usage.wait_settled()

                # Audit rows the guards wrote during this turn → guard verdicts.
                audit_result = await db.execute(
                    select(AuditLog).where(
                        AuditLog.conversation_id == conversation_id,
                        AuditLog.action.in_(("input_guard_blocked", "output_guard_blocked")),
                    ).order_by(AuditLog.created_at)
                )
                turn_audit = [r for r in audit_result.scalars().all() if r.id not in prior_audit_ids]
                events = merge_live(sink.events, derive_events(final_output, turn_audit))
                payload = turn_payload(turn_index, events, latency_ms, usage.as_dict(), final_output)

                # Pipeline steps first, then the response.
                yield _sse("turn", payload)

                # Stream response word-by-word
                response_text = final_output.get("response", "")
                for word in response_text.split(" "):
                    yield ServerSentEvent(data=word + " ", event="token")

                # Determine what services were called (for audit logging)
                actions_taken = final_output.get("actions_taken", [])
                services_called = [a.get("service", "") for a in actions_taken]
                action = "search_kb" if "knowledge_service" in services_called else "conversation_response"

                # Persist agent message
                agent_msg = Message(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation_id,
                    role="agent",
                    content=response_text,
                    agent_type="conversation",
                )
                db.add(agent_msg)
                await db.flush()

                # Persist audit log
                db.add(AuditLog(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation_id,
                    message_id=agent_msg.id,
                    agent_type="conversation",
                    action=action,
                    input_data={"query": latest_msg_content},
                    output_data={
                        "response_length": len(response_text),
                        "actions_taken": actions_taken,
                    },
                    routing_decision=", ".join(services_called) if services_called else "direct_response",
                    confidence=final_output.get("confidence", 0.0),
                ))

                # Persist turn state for next turn (or clear it on escalation).
                # Done in the same commit as the message — if either fails, both roll back.
                # Uses a direct UPDATE rather than mutating the ORM object to avoid
                # SQLAlchemy change-tracking issues inside the generator closure.
                new_turn_state = next_turn_state(final_output)
                await db.execute(
                    sa_update(Conversation)
                    .where(Conversation.id == conversation_id)
                    .values(turn_state=new_turn_state)
                )
                await db.commit()

                await _persist_turn(db, conversation_id, payload, latest_msg_id, agent_msg.id, turn_started_at)
                yield _sse("turn_complete", payload)

                # Tell the frontend the conversation just escalated so it can
                # disable the input — the next stream request would short-circuit
                # anyway, but the customer shouldn't discover that by typing.
                if final_output.get("requires_escalation"):
                    yield ServerSentEvent(data="", event="escalated")

                yield ServerSentEvent(data="", event="done")

            except Exception:
                # Never stream exception detail to the customer-facing channel —
                # log it server-side and send a canned apology instead.
                logger.exception("Chat stream failed for conversation %s", conversation_id)
                latency_ms = int((time.monotonic() - t0) * 1000)
                escalated = await _record_turn_failure(db, conversation_id, "graph_error")
                payload = turn_payload(
                    turn_index,
                    error_events("graph_error", "Something went wrong while running the agent."),
                    latency_ms, usage.as_dict(),
                    {"requires_escalation": escalated,
                     "escalation_reason": "unhandled_error" if escalated else ""},
                    error="graph_error",
                )
                await _persist_turn(db, conversation_id, payload, latest_msg_id, None, turn_started_at)
                yield _sse("turn", payload)
                if escalated:
                    yield ServerSentEvent(
                        data="I'm having repeated trouble on my end, so I've passed this to a specialist who will follow up with you.",
                        event="token",
                    )
                    yield _sse("turn_complete", payload)
                    yield ServerSentEvent(data="", event="escalated")
                else:
                    yield ServerSentEvent(
                        data="Something went wrong on our end — please try again in a moment.",
                        event="token",
                    )
                    yield _sse("turn_complete", payload)
                yield ServerSentEvent(data="", event="done")

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Test mode endpoint — only available when APP_ENV=test
# ---------------------------------------------------------------------------

class TestChatRequest(BaseModel):
    """Request body for the test endpoint.

    Full agent run mode (default):
        - messages: conversation history [{"role": "customer"/"agent", "content": str}]
        - mock_context: injected as customer_context in state (replaces DB lookup)
        - customer_id: any string; used as customer_id in state
        - mock_agent_state: pre-load agent state fields as if prior turns already ran.
          Supported keys: actions_taken, action_results, service_call_count,
          retrieved_context, consecutive_blocks, last_clarification_source,
          customer_context, consecutive_small_talk, total_small_talk,
          provocative_count, consecutive_high_negative. Merged over initial_state after defaults are set,
          so any key present here overrides the default.

    Output guard test mode (test_output_guard=True):
        - agent_response: the response to evaluate
        - tools_called: [{"tool": "cancel_order", "args": {...}}] — tools that were called
        - known_ids: {"order_ids": [...], "customer_id": "...", "customer_name": "..."}
          — IDs legitimately in context. customer_name (optional) becomes
          customer_context.name, as it is in production.

    Traceability (optional):
        - test_id: eval case ID (e.g. "IG-017") — attached as LangSmith tag + metadata
        - version_tag: eval run tag (e.g. "v1.1") — attached as LangSmith tag + metadata
    """
    customer_id: str = "test-customer"
    messages: List[dict] = Field(default_factory=list)
    mock_context: dict = Field(default_factory=dict)
    mock_agent_state: dict = Field(default_factory=dict)
    # When True (default), full-agent runs skip the output-guard LLM call —
    # keeps guard API flakiness out of agent-behavior evals. Pass False to run
    # the real guard (the eval runner's --output-guard on flag sets this).
    # Ignored in output-guard test mode, which always runs the real guard.
    disable_output_guard: bool = True
    # Output guard test mode
    test_output_guard: bool = False
    agent_response: str = ""
    tools_called: List[dict] = Field(default_factory=list)
    known_ids: dict = Field(default_factory=dict)
    # LangSmith traceability
    test_id: str = ""
    version_tag: str = ""



def next_turn_state(final_state: dict) -> "dict | None":
    """What carries into the next turn (conversations.turn_state). None after an
    escalation — the conversation is handed off. Shared by the live stream and
    the eval endpoint's turn_state, so simulated multi-turn evals carry exactly
    what production carries."""
    if final_state.get("requires_escalation"):
        return None
    return {
        "actions_taken": final_state.get("actions_taken", []),
        "consecutive_blocks": final_state.get("consecutive_blocks", 0),
        "consecutive_small_talk": final_state.get("consecutive_small_talk", 0),
        "total_small_talk": final_state.get("total_small_talk", 0),
        "provocative_count": final_state.get("provocative_count", 0),
        "consecutive_high_negative": final_state.get("consecutive_high_negative", 0),
        "last_clarification_source": final_state.get("last_clarification_source", ""),
    }


class TestChatResponse(BaseModel):
    # Full agent run fields
    response: str = ""
    actions_taken: List[dict] = Field(default_factory=list)
    confidence: float = 0.0
    inferred_intent: str = ""
    requires_escalation: bool = False
    escalation_reason: str = ""
    context_summary: str = ""
    input_guard_blocked: bool = False
    input_guard_reason: str = ""
    # Output guard test mode fields
    output_guard_verdict: str = ""   # "pass" or "block"
    output_guard_failure_type: str = ""
    output_guard_handoff_intent: bool = False  # real guard's handoff detection (pass verdicts only)
    # Token usage estimates for cost tracking (agent-side LLM calls)
    # These are rough estimates (chars ÷ 4) since we can't hook into graph internals.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Debug fields — only populated when debug_classifier: true is set in mock_context
    classifier_reasoning: str = ""
    # run_trace events for this request: fallbacks that fired and output-guard
    # verdicts with the guard's explanation (see backend/agents/run_trace.py)
    trace: List[dict] = Field(default_factory=list)
    # This turn's raw tool results (the dicts the response LLM saw), so judges
    # can check stated facts such as return_deadline against the tool.
    action_results: List[dict] = Field(default_factory=list)
    # State the next turn would start from (next_turn_state); None after escalation.
    # Fed back as mock_agent_state by simulated multi-turn evals.
    turn_state: Optional[dict] = None


@router.post("/chat/test", response_model=TestChatResponse)
async def test_chat(
    body: TestChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Eval-only endpoint: runs the agent with optional mock context injection.

    Gated by APP_ENV=test — returns 404 in all other environments.
    Never persists messages or audit logs to the database.
    Escalation handler skips DB writes when conversation_id is absent (by design).
    """
    if settings.app_env != "test":
        raise HTTPException(status_code=404, detail="Not found")

    # --- Output guard test mode ---
    if body.test_output_guard:
        from backend.guardrails.output_guard import check_output  # noqa: PLC0415

        # Translate tools_called [{"tool": "cancel_order", "args": {...}, "result": {...}}]
        # into the actions_taken / action_results format that the output guard reads.
        # "result" is optional in the test schema — omitting it means the tool ran but
        # returned nothing (treated as an empty dict).
        synthetic_actions_taken = [
            {
                "action": t.get("tool", ""),
                "service": "action_service",
                "params": t.get("args", {}),
                "success": True,
            }
            for t in body.tools_called
        ]
        synthetic_action_results = [
            t.get("result", {})
            for t in body.tools_called
        ]
        # Build known order IDs into customer_context so the guard can find them
        synthetic_state: AgentState = {
            "messages": [{"role": "customer", "content": "test"}],
            "customer_id": body.customer_id,
            "customer_context": {
                "name": body.known_ids.get("customer_name", ""),
                "recent_orders": [
                    {"order_id": oid}
                    for oid in body.known_ids.get("order_ids", [])
                ],
            },
            "retrieved_context": [],
            "action_results": synthetic_action_results,
            "confidence": 1.0,
            "requires_escalation": False,
            "escalation_reason": "",
            "actions_taken": synthetic_actions_taken,
            "prior_turn_actions": [],
            "response": "",
            "pending_service": "",
            "pending_action": {},
        }
        guard_result = await check_output(body.agent_response, synthetic_state)
        return TestChatResponse(
            output_guard_verdict="pass" if guard_result.get("safe") else "block",
            output_guard_failure_type="none" if guard_result.get("safe") else guard_result.get("reason", "unknown"),
            output_guard_handoff_intent=bool(guard_result.get("handoff_intent", False)),
        )

    # --- Full agent run mode ---
    # run_trace collects fallbacks + output-guard verdicts for this request
    # (each request runs in its own context, so nothing leaks across requests).
    from backend.agents import run_trace  # noqa: PLC0415
    trace_token = run_trace.start()

    # Check input guard explicitly so we can report its result in the response
    from backend.guardrails.input_guard import check_input  # noqa: PLC0415

    last_customer_msg = ""
    for msg in reversed(body.messages):
        if msg.get("role") == "customer":
            last_customer_msg = msg.get("content", "")
            break

    input_guard_blocked = False
    input_guard_reason = ""
    if last_customer_msg:
        guard_result = await check_input(last_customer_msg)
        if not guard_result.get("safe"):
            # Report the block for the Input Guard judge, but still run the graph:
            # production block handling (redirect generation, consecutive-block
            # counting, 3rd-strike and abusive escalation) lives in the graph's
            # pass 1, and the old early return made all of it untestable.
            input_guard_blocked = True
            input_guard_reason = guard_result.get("reason", "")

    mock_context = body.mock_context or {}

    # Validate mock_context keys — unknown keys are likely typos that would be
    # silently ignored. Any new key added to mock_context must be listed here.
    _KNOWN_MOCK_CONTEXT_KEYS = {
        # Customer identity fields
        "name", "email", "phone", "address", "customer_id", "account_type",
        # Order / refund data for mock tool layer
        "orders", "refunds",
        # Behavioural injection flags (test-only)
        "debug_classifier",   # enables classifier_reasoning in response
        "mock_handoff_intent",  # injects handoff_intent=True into output guard result
    }
    unknown_keys = set(mock_context.keys()) - _KNOWN_MOCK_CONTEXT_KEYS
    if unknown_keys:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown mock_context keys: {sorted(unknown_keys)}. "
                   f"Add them to _KNOWN_MOCK_CONTEXT_KEYS in chat.py if intentional.",
        )

    # customer_context mirrors production's get_customer_context: the name plus
    # the recent-orders summary (built from the fixture's orders when it defines
    # them). Full order data stays behind the tool calls via
    # configurable["mock_account_state"], as in production.
    customer_ctx = {"name": mock_context.get("name") or "Test Customer"} if mock_context else {}
    if "orders" in mock_context:
        from backend.agents.mock_tools import mock_recent_orders  # noqa: PLC0415
        customer_ctx["recent_orders"] = mock_recent_orders(
            mock_context, get_settings().recent_orders_in_context,
        )

    initial_state: AgentState = {
        "messages": body.messages,
        "customer_id": body.customer_id,
        "customer_context": customer_ctx,
        "retrieved_context": [],
        "action_results": [],
        "confidence": 0.0,
        "requires_escalation": False,
        "escalation_reason": "",
        "actions_taken": [],
        "prior_turn_actions": [],
        "response": "",
        "pending_service": "",
        "pending_action": {},
        "inferred_intent": "",
        "last_clarification_source": "",
        "context_summary": "",
        "consecutive_blocks": 0,
        "consecutive_small_talk": 0,
        "total_small_talk": 0,
        "provocative_count": 0,
        "consecutive_high_negative": 0,
        "service_call_count": 0,
        "classifier_reasoning": "",
    }

    # Pre-load agent state from prior turns. Only allowed keys are merged to
    # prevent accidental override of routing fields (pending_service etc.).
    # mock_agent_state["actions_taken"] maps to prior_turn_actions so that the
    # service_ran gate (which checks actions_taken) is not triggered by historical entries.
    # action_results is intentionally excluded — it is always [] at turn start in production
    # (cleared between turns). Injecting prior-turn results would give the agent context
    # it would never have in a real conversation.
    _ALLOWED_MOCK_STATE_KEYS = {
        "service_call_count",
        "retrieved_context", "consecutive_blocks", "last_clarification_source",
        "customer_context",
        "consecutive_small_talk", "total_small_talk", "provocative_count",
        "consecutive_high_negative",
    }
    if body.mock_agent_state:
        for key, value in body.mock_agent_state.items():
            if key == "actions_taken":
                initial_state["prior_turn_actions"] = value
            elif key in _ALLOWED_MOCK_STATE_KEYS:
                initial_state[key] = value

    from backend.agents.graph import graph  # noqa: PLC0415

    # Build LangSmith tags + metadata from traceability fields if provided
    tags = ["eval"]
    metadata = {}
    if body.test_id:
        tags.append(body.test_id)
        metadata["test_id"] = body.test_id
    if body.version_tag:
        tags.append(body.version_tag)
        metadata["version_tag"] = body.version_tag

    # conversation_id="" — escalation handler skips DB writes when absent
    # mock_account_state — present only when mock_context provided; feeds mock tool layer
    # debug_classifier — present only when mock_context sets debug_classifier: true
    # mock_handoff_intent — present only when mock_context sets mock_handoff_intent: true;
    #   causes check_output to return handoff_intent=True when the guard is bypassed,
    #   exercising the output-guard escalation path without a real LLM guard call
    # disable_output_guard — bypasses the output-guard LLM call for this run
    configurable: dict = {"db": db, "conversation_id": ""}
    if body.disable_output_guard:
        configurable["disable_output_guard"] = True
    if body.mock_context:
        configurable["mock_account_state"] = body.mock_context
        if body.mock_context.get("debug_classifier"):
            configurable["debug_classifier"] = True
        if body.mock_context.get("mock_handoff_intent"):
            configurable["mock_handoff_intent"] = True
    config = {
        "configurable": configurable,
        "tags": tags,
        "metadata": metadata,
    }
    try:
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state, config=config),
            timeout=90,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="api_timeout")

    inferred_intent = final_state.get("inferred_intent") or "general"

    response_text = final_state.get("response", "")

    # Rough token estimates (chars ÷ 4) for cost tracking in the eval runner.
    # Includes system-prompt overhead (~700 tokens) and context injected by the graph.
    input_chars = sum(len(str(m.get("content", ""))) for m in body.messages)
    input_chars += len(str(body.mock_context))
    prompt_tokens = input_chars // 4 + 700
    completion_tokens = len(response_text) // 4 + 50

    return TestChatResponse(
        response=response_text,
        actions_taken=final_state.get("actions_taken") or [],
        confidence=final_state.get("confidence", 0.0),
        inferred_intent=inferred_intent,
        requires_escalation=final_state.get("requires_escalation", False),
        escalation_reason=final_state.get("escalation_reason", ""),
        context_summary=final_state.get("context_summary", ""),
        classifier_reasoning=final_state.get("classifier_reasoning", ""),
        input_guard_blocked=input_guard_blocked,
        input_guard_reason=input_guard_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        trace=run_trace.stop(trace_token),
        action_results=[r for r in (final_state.get("action_results") or []) if isinstance(r, dict)],
        turn_state=next_turn_state(final_state),
    )
