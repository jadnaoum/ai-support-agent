import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
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
    initial_state: AgentState = {
        "messages": messages_for_state,
        "customer_id": str(conversation.customer_id),
        "customer_context": customer_context,
        "retrieved_context": [],
        "action_results": [],
        "confidence": 0.0,
        "requires_escalation": False,
        "escalation_reason": "",
        "actions_taken": [],
        "response": "",
        "pending_service": "",
        "pending_action": {},
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
            await db.commit()

            yield ServerSentEvent(data="", event="done")

        except Exception as e:
            yield ServerSentEvent(data=str(e), event="error")

    return EventSourceResponse(event_generator())
