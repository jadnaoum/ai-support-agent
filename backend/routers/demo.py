"""Demo experience endpoints — products, scenario catalog, demo customers,
business rules. Read `docs/frontend_demo_plan_v2.md` for the design."""
import random
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.session import get_db
from backend.db.models import Product, Conversation, Message, Turn
from backend.demo.checks import compute_checks
from backend.demo.rails import hash_ip
from backend.demo.scenarios import (
    NAME_POOL, RELATIVE_DATES, ORDER_STATUSES, SCENARIOS, COMMON_PROMPTS,
    MAX_ORDERS_PER_CUSTOMER, scenario_by_id, product_id_for,
)
from backend.demo.customers import (
    DemoValidationError, create_demo_customer, load_customer, serialize_customer,
    count_recent_demo_customers,
)
from backend.demo.rules import rules_for

settings = get_settings()
router = APIRouter(prefix="/api", tags=["demo"])


class OrderSpec(BaseModel):
    product_id: str
    status: str
    ordered_relative: str


class CreateDemoCustomerRequest(BaseModel):
    name: str
    scenario_id: Optional[str] = None
    orders: List[OrderSpec] = Field(default_factory=list)


@router.get("/products")
async def list_products(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).order_by(Product.name))
    return [{"id": p.id, "name": p.name, "category": p.category} for p in result.scalars().all()]


@router.get("/demo/catalog")
async def demo_catalog(db: AsyncSession = Depends(get_db)):
    """Everything the setup page needs in one call. Names are shuffled server-side
    so each load gets a different cast; the client assigns them to scenarios."""
    result = await db.execute(select(Product))
    products = {p.id: p for p in result.scalars().all()}

    def order_view(o: dict) -> dict:
        pid = product_id_for(o["product"])
        p = products.get(pid)
        return {
            "product_id": pid,
            "product_name": p.name if p else o["product"],
            "category": p.category if p else "",
            "status": o["status"],
            "ordered_relative": o["ordered_relative"],
            "ordered_label": RELATIVE_DATES[o["ordered_relative"]][1],
        }

    names = list(NAME_POOL)
    random.shuffle(names)
    return {
        "names": names,
        "scenarios": [
            {
                "id": s["id"],
                "title": s["title"],
                "dare": s["dare"],
                "orders": [order_view(o) for o in s["orders"]],
                "suggested_prompts": s["suggested_prompts"] + COMMON_PROMPTS,
            }
            for s in SCENARIOS
        ],
        "statuses": ORDER_STATUSES,
        "relative_dates": [{"id": k, "label": v[1]} for k, v in RELATIVE_DATES.items()],
        "max_orders": MAX_ORDERS_PER_CUSTOMER,
        "common_prompts": COMMON_PROMPTS,
    }


@router.post("/demo/customer", status_code=201)
async def create_customer(
    body: CreateDemoCustomerRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip_hash = hash_ip(request.client.host if request.client else None)
    recent = await count_recent_demo_customers(db, ip_hash, hours=24)
    if recent >= settings.demo_max_customers_per_ip_per_day:
        raise HTTPException(
            status_code=429,
            detail={"code": "customer_cap", "max_per_day": settings.demo_max_customers_per_ip_per_day},
        )

    scenario_id = ""
    if body.scenario_id:
        scenario = scenario_by_id(body.scenario_id)
        if not scenario:
            raise HTTPException(status_code=422, detail={"code": "unknown_scenario"})
        scenario_id = scenario["id"]
        orders = scenario["orders"]
    else:
        orders = [o.model_dump() for o in body.orders]

    try:
        customer = await create_demo_customer(
            db, body.name, orders, ip_hash=ip_hash, scenario_id=scenario_id,
        )
    except DemoValidationError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_request", "message": str(e)})

    customer = await load_customer(db, customer.id)
    data = serialize_customer(customer)
    data["suggested_prompts"] = _suggested_prompts(scenario_id)
    return data


@router.get("/demo/customer/{customer_id}")
async def get_customer(customer_id: str, db: AsyncSession = Depends(get_db)):
    customer = await load_customer(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    data = serialize_customer(customer)
    data["suggested_prompts"] = _suggested_prompts(data["scenario_id"])
    return data


@router.get("/demo/rules")
async def demo_rules(customer_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    customer = await load_customer(db, customer_id) if customer_id else None
    if customer_id and not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return await rules_for(db, customer)


def _suggested_prompts(scenario_id: str) -> list:
    scenario = scenario_by_id(scenario_id) if scenario_id else None
    own = scenario["suggested_prompts"] if scenario else ["What can you help me with?"]
    return own + COMMON_PROMPTS


@router.get("/conversations/{conversation_id}/timeline")
async def conversation_timeline(conversation_id: str, db: AsyncSession = Depends(get_db)):
    """Everything the chat page needs to render or replay a conversation:
    customer, messages, per-turn pipeline events, totals and the checks."""
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.turns))
        .where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    customer = await load_customer(db, conversation.customer_id)
    customer_data = None
    if customer:
        customer_data = serialize_customer(customer)
        customer_data["suggested_prompts"] = _suggested_prompts(customer_data["scenario_id"])

    msg_result = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
    )
    messages = msg_result.scalars().all()
    by_id = {m.id: m for m in messages}

    turns = []
    for t in sorted(conversation.turns, key=lambda t: t.index):
        cm = by_id.get(t.customer_message_id)
        am = by_id.get(t.agent_message_id)
        turns.append({
            "index": t.index,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "customer_message": cm.content if cm else None,
            "response": am.content if am else None,
            "events": t.events or [],
            "latency_ms": t.latency_ms,
            "llm_calls": t.llm_calls,
            "prompt_tokens": t.prompt_tokens,
            "completion_tokens": t.completion_tokens,
            "cost_usd": float(t.cost_usd) if t.cost_usd is not None else None,
            "cost_basis": t.cost_basis,
            "escalation_reason": t.escalation_reason,
            "context_summary": t.context_summary,
            "error": t.error,
        })

    priced = [t for t in turns if t["cost_usd"] is not None]
    totals = {
        "messages": sum(1 for m in messages if m.role == "customer"),
        "turns": len(turns),
        "latency_ms": sum(t["latency_ms"] or 0 for t in turns),
        "llm_calls": sum(t["llm_calls"] or 0 for t in turns),
        "cost_usd": round(sum(t["cost_usd"] for t in priced), 6) if priced else None,
        "cost_basis": (
            "unavailable" if not priced
            else "measured" if all(t["cost_basis"] == "measured" for t in priced) and len(priced) == len(turns)
            else "partial"
        ),
    }

    return {
        "conversation": {
            "id": conversation.id,
            "status": conversation.status,
            "started_at": conversation.started_at.isoformat() if conversation.started_at else None,
            "ended_at": conversation.ended_at.isoformat() if conversation.ended_at else None,
            "csat": (
                {"rating": conversation.csat_score, "tags": conversation.csat_tags or [],
                 "free_text": conversation.csat_comment}
                if conversation.csat_score is not None else None
            ),
        },
        "customer": customer_data,
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content,
             "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in messages if m.role in ("customer", "agent")
        ],
        "turns": turns,
        "totals": totals,
        "checks": compute_checks(turns, own_email=(customer.email if customer else "")),
    }
