"""Demo experience endpoints — products, scenario catalog, demo customers,
business rules. Read `docs/frontend_demo_plan_v2.md` for the design."""
import random
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.session import get_db
from backend.db.models import Product
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
