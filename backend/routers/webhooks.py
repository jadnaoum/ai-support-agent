from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_db
from backend.db.models import Conversation

router = APIRouter(prefix="/api", tags=["webhooks"])


class CSATRequest(BaseModel):
    conversation_id: str
    score: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


class CSATResponse(BaseModel):
    conversation_id: str
    score: int
    message: str


@router.post("/csat", response_model=CSATResponse)
async def submit_csat(
    body: CSATRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Conversation).where(Conversation.id == body.conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.status == "active":
        raise HTTPException(status_code=400, detail="Cannot rate an active conversation")
    if conversation.csat_score is not None:
        raise HTTPException(status_code=409, detail="CSAT already submitted for this conversation")

    conversation.csat_score = body.score
    conversation.csat_comment = body.comment
    await db.commit()

    return CSATResponse(
        conversation_id=body.conversation_id,
        score=body.score,
        message="Thank you for your feedback!",
    )
