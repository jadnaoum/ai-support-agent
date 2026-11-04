from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, AliasChoices, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_db
from backend.db.models import Conversation

router = APIRouter(prefix="/api", tags=["webhooks"])

# Structured feedback tags. Negative tags are offered for 1-3 stars, positive
# for 4-5 — the endpoint enforces the band so the data stays analysable.
NEGATIVE_TAGS: tuple = (
    "made_things_up", "didnt_understand", "going_in_circles", "sounded_robotic", "too_slow",
)
POSITIVE_TAGS: tuple = (
    "quick_and_easy", "agent_understood_me", "accurate_information",
    "felt_like_real_person", "handled_tricky_situation",
)
CSAT_TAGS: frozenset = frozenset(NEGATIVE_TAGS + POSITIVE_TAGS)


class CSATRequest(BaseModel):
    conversation_id: str
    # `rating` is the current name; `score` is kept as an alias for older clients.
    rating: int = Field(..., ge=1, le=5, validation_alias=AliasChoices("rating", "score"))
    tags: List[str] = Field(default_factory=list)
    free_text: Optional[str] = Field(None, validation_alias=AliasChoices("free_text", "comment"))

    @field_validator("tags")
    @classmethod
    def _known_tags(cls, tags: List[str]) -> List[str]:
        unknown = [t for t in tags if t not in CSAT_TAGS]
        if unknown:
            raise ValueError(f"unknown tags: {unknown}")
        return list(dict.fromkeys(tags))  # de-duplicate, keep order

    @model_validator(mode="after")
    def _tags_match_band(self):
        allowed = NEGATIVE_TAGS if self.rating <= 3 else POSITIVE_TAGS
        off_band = [t for t in self.tags if t not in allowed]
        if off_band:
            raise ValueError(f"tags {off_band} are not valid for a {self.rating}-star rating")
        return self


class CSATResponse(BaseModel):
    conversation_id: str
    rating: int
    tags: List[str]
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

    conversation.csat_score = body.rating
    conversation.csat_comment = body.free_text
    conversation.csat_tags = body.tags
    await db.commit()

    return CSATResponse(
        conversation_id=body.conversation_id,
        rating=body.rating,
        tags=body.tags,
        message="Thank you for your feedback!",
    )
