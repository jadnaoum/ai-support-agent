import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from backend.config import get_settings
from backend.db.session import get_db
from backend.db.models import Conversation, Message, Customer, AuditLog
from backend.agents.state import AgentState
from backend.tools.customer_tools import get_customer_context

settings = get_settings()
router = APIRouter(prefix="/api", tags=["chat"])


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

    # 3. Load conversation history for agent state
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(settings.max_context_messages)
    )
    all_messages = result.scalars().all()
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
        "service_call_count": 0,
    }

    # Lazy import — avoids LangGraph compile() running at module load time,
    # which conflicts with pytest-asyncio's event loop.
    from backend.agents.graph import graph  # noqa: PLC0415

    # 6. SSE event generator
    async def event_generator():
        # Accumulate state updates across all nodes in the graph
        final_output: dict = {}
        try:
            config = {"configurable": {"db": db, "conversation_id": conversation_id}}
            async for chunk in graph.astream(initial_state, config=config, stream_mode="updates"):
                for node_output in chunk.values():
                    if isinstance(node_output, dict):
                        final_output.update(node_output)

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
                input_data={"query": latest_msg.content},
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
            if final_output.get("requires_escalation"):
                new_turn_state = None
            else:
                new_turn_state = {
                    "actions_taken": final_output.get("actions_taken", []),
                    "consecutive_blocks": final_output.get("consecutive_blocks", 0),
                    "last_clarification_source": final_output.get("last_clarification_source", ""),
                }
            await db.execute(
                sa_update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(turn_state=new_turn_state)
            )
            await db.commit()

            yield ServerSentEvent(data="", event="done")

        except Exception as e:
            yield ServerSentEvent(data=str(e), event="error")

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
          customer_context. Merged over initial_state after defaults are set,
          so any key present here overrides the default.

    Output guard test mode (test_output_guard=True):
        - agent_response: the response to evaluate
        - tools_called: [{"tool": "cancel_order", "args": {...}}] — tools that were called
        - known_ids: {"order_ids": [...], "customer_id": "..."} — IDs legitimately in context

    Traceability (optional):
        - test_id: eval case ID (e.g. "IG-017") — attached as LangSmith tag + metadata
        - version_tag: eval run tag (e.g. "v1.1") — attached as LangSmith tag + metadata
    """
    customer_id: str = "test-customer"
    messages: List[dict] = Field(default_factory=list)
    mock_context: dict = Field(default_factory=dict)
    mock_agent_state: dict = Field(default_factory=dict)
    # Output guard test mode
    test_output_guard: bool = False
    agent_response: str = ""
    tools_called: List[dict] = Field(default_factory=list)
    known_ids: dict = Field(default_factory=dict)
    # LangSmith traceability
    test_id: str = ""
    version_tag: str = ""


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
    # Token usage estimates for cost tracking (agent-side LLM calls)
    # These are rough estimates (chars ÷ 4) since we can't hook into graph internals.
    prompt_tokens: int = 0
    completion_tokens: int = 0


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
                "recent_orders": [
                    {"order_id": oid}
                    for oid in body.known_ids.get("order_ids", [])
                ]
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
        )

    # --- Full agent run mode ---

    # Check input guard explicitly so we can report its result in the response
    from backend.guardrails.input_guard import check_input  # noqa: PLC0415

    last_customer_msg = ""
    for msg in reversed(body.messages):
        if msg.get("role") == "customer":
            last_customer_msg = msg.get("content", "")
            break

    if last_customer_msg:
        guard_result = await check_input(last_customer_msg)
        if not guard_result.get("safe"):
            return TestChatResponse(
                response=guard_result.get("blocked_response", ""),
                input_guard_blocked=True,
                input_guard_reason=guard_result.get("reason", ""),
            )

    initial_state: AgentState = {
        "messages": body.messages,
        "customer_id": body.customer_id,
        "customer_context": body.mock_context,
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
        "service_call_count": 0,
    }

    # Pre-load agent state from prior turns. Only allowed keys are merged to
    # prevent accidental override of routing fields (pending_service etc.).
    # mock_agent_state["actions_taken"] maps to prior_turn_actions so that the
    # service_ran gate (which checks actions_taken) is not triggered by historical entries.
    _ALLOWED_MOCK_STATE_KEYS = {
        "action_results", "service_call_count",
        "retrieved_context", "consecutive_blocks", "last_clarification_source",
        "customer_context",
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
    configurable: dict = {"db": db, "conversation_id": ""}
    if body.mock_context:
        configurable["mock_account_state"] = body.mock_context
    config = {
        "configurable": configurable,
        "tags": tags,
        "metadata": metadata,
    }
    final_state = await graph.ainvoke(initial_state, config=config)

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
        input_guard_blocked=False,
        input_guard_reason="",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
