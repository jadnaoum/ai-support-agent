import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_db
from backend.db.models import Conversation, Message, Customer

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

    # Agent processing will be wired up in Phase 2
    return SendMessageResponse(
        conversation_id=body.conversation_id,
        message_id=message.id,
    )


@router.get("/chat/stream/{conversation_id}")
async def stream_response(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """SSE endpoint — agent streaming wired up in Phase 2."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Placeholder until Phase 2 agent integration
    raise HTTPException(status_code=501, detail="Streaming not yet implemented — coming in Phase 2")
