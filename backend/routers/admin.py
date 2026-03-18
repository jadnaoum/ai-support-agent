from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.session import get_db
from backend.db.models import Conversation, Message, AuditLog, Escalation

router = APIRouter(prefix="/api", tags=["admin"])


class ConversationSummary(BaseModel):
    id: str
    customer_id: str
    status: str
    started_at: datetime
    ended_at: Optional[datetime]
    csat_score: Optional[int]
    message_count: int

    class Config:
        from_attributes = True


class MetricsResponse(BaseModel):
    total_conversations: int
    active_conversations: int
    resolved_conversations: int
    escalated_conversations: int
    escalation_rate: float
    avg_csat: Optional[float]
    csat_count: int


@router.get("/conversations")
async def list_conversations(
    status: Optional[str] = Query(None, description="Filter by status: active, resolved, escalated"),
    customer_id: Optional[str] = Query(None),
    csat_min: Optional[int] = Query(None, ge=1, le=5),
    csat_max: Optional[int] = Query(None, ge=1, le=5),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if status:
        filters.append(Conversation.status == status)
    if customer_id:
        filters.append(Conversation.customer_id == customer_id)
    if csat_min is not None:
        filters.append(Conversation.csat_score >= csat_min)
    if csat_max is not None:
        filters.append(Conversation.csat_score <= csat_max)

    query = (
        select(Conversation)
        .where(and_(*filters) if filters else True)
        .order_by(Conversation.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    conversations = result.scalars().all()

    # Get message counts
    count_query = select(
        Message.conversation_id,
        func.count(Message.id).label("message_count"),
    ).group_by(Message.conversation_id)
    count_result = await db.execute(count_query)
    message_counts = {row.conversation_id: row.message_count for row in count_result}

    return [
        {
            "id": c.id,
            "customer_id": c.customer_id,
            "status": c.status,
            "started_at": c.started_at,
            "ended_at": c.ended_at,
            "csat_score": c.csat_score,
            "message_count": message_counts.get(c.id, 0),
        }
        for c in conversations
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Conversation)
        .options(
            selectinload(Conversation.messages),
            selectinload(Conversation.audit_logs),
            selectinload(Conversation.escalations),
        )
        .where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "id": conversation.id,
        "customer_id": conversation.customer_id,
        "status": conversation.status,
        "started_at": conversation.started_at,
        "ended_at": conversation.ended_at,
        "summary": conversation.summary,
        "csat_score": conversation.csat_score,
        "csat_comment": conversation.csat_comment,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "agent_type": m.agent_type,
                "created_at": m.created_at,
            }
            for m in sorted(conversation.messages, key=lambda x: x.created_at or datetime.min)
        ],
        "audit_logs": [
            {
                "id": a.id,
                "agent_type": a.agent_type,
                "action": a.action,
                "routing_decision": a.routing_decision,
                "confidence": a.confidence,
                "created_at": a.created_at,
            }
            for a in sorted(conversation.audit_logs, key=lambda x: x.created_at or datetime.min)
        ],
        "escalations": [
            {
                "id": e.id,
                "reason": e.reason,
                "agent_confidence": e.agent_confidence,
                "context_summary": e.context_summary,
                "created_at": e.created_at,
            }
            for e in conversation.escalations
        ],
    }


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics(db: AsyncSession = Depends(get_db)):
    total_result = await db.execute(select(func.count(Conversation.id)))
    total = total_result.scalar() or 0

    status_result = await db.execute(
        select(Conversation.status, func.count(Conversation.id))
        .group_by(Conversation.status)
    )
    status_counts = {row[0]: row[1] for row in status_result}

    csat_result = await db.execute(
        select(func.avg(Conversation.csat_score), func.count(Conversation.csat_score))
        .where(Conversation.csat_score.isnot(None))
    )
    csat_row = csat_result.one()
    avg_csat = float(csat_row[0]) if csat_row[0] else None
    csat_count = csat_row[1] or 0

    escalated = status_counts.get("escalated", 0)
    escalation_rate = round(escalated / total, 4) if total > 0 else 0.0

    return MetricsResponse(
        total_conversations=total,
        active_conversations=status_counts.get("active", 0),
        resolved_conversations=status_counts.get("resolved", 0),
        escalated_conversations=escalated,
        escalation_rate=escalation_rate,
        avg_csat=avg_csat,
        csat_count=csat_count,
    )
