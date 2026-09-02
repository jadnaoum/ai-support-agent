"""Demo customer creation and serialisation.

Demo customers are real rows — the agent runs the real tools against them.
They carry ``metadata.demo = true`` so scripts/purge_demo_customers.py can
clean them up, and never a raw IP (only a hash for the per-IP cap).
"""
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.models import Customer, Product, Order, OrderItem, Refund
from backend.demo.scenarios import (
    RELATIVE_DATES, ORDER_STATUSES, MAX_ORDERS_PER_CUSTOMER, product_id_for,
)

DEMO_EMAIL_DOMAIN = "shire.demo"
_NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' \-]{0,39}$")


class DemoValidationError(ValueError):
    pass


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise DemoValidationError("name must be 1-40 letters, spaces, apostrophes or hyphens")
    return name


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "visitor"


def demo_email(name: str) -> str:
    return f"{slugify(name)}-{uuid.uuid4().hex[:6]}@{DEMO_EMAIL_DOMAIN}"


def normalize_orders(orders: list) -> list:
    """Validate the order spec list. Accepts either ``product_id`` (catalog UUID)
    or ``product`` (seed key, used by scenarios). Returns dicts with product_id."""
    if not orders:
        raise DemoValidationError("at least one order is required")
    if len(orders) > MAX_ORDERS_PER_CUSTOMER:
        raise DemoValidationError(f"at most {MAX_ORDERS_PER_CUSTOMER} orders per customer")
    out = []
    for o in orders:
        pid = o.get("product_id") or (product_id_for(o["product"]) if o.get("product") else None)
        if not pid:
            raise DemoValidationError("each order needs a product_id")
        status = o.get("status")
        if status not in ORDER_STATUSES:
            raise DemoValidationError(f"status must be one of {ORDER_STATUSES}")
        rel = o.get("ordered_relative")
        if rel not in RELATIVE_DATES:
            raise DemoValidationError(f"ordered_relative must be one of {sorted(RELATIVE_DATES)}")
        out.append({"product_id": pid, "status": status, "ordered_relative": rel})
    return out


def order_dates(ordered_relative: str, status: str, now: datetime) -> tuple:
    """(created_at, delivered_at). Delivered/returned orders MUST carry a
    delivered_at — the return-window check is silently skipped without one."""
    days, _ = RELATIVE_DATES[ordered_relative]
    created_at = now - timedelta(days=days)
    delivered_at = None
    if status in ("delivered", "returned"):
        delivered_at = min(created_at + timedelta(days=2), now - timedelta(hours=1))
    return created_at, delivered_at


async def count_recent_demo_customers(db: AsyncSession, ip_hash: str, hours: int = 24) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        select(func.count(Customer.id)).where(
            Customer.metadata_["demo"].as_boolean().is_(True),
            cast(Customer.metadata_["ip_hash"], String) == f'"{ip_hash}"',
            Customer.created_at >= since,
        )
    )
    return int(result.scalar() or 0)


async def create_demo_customer(
    db: AsyncSession, name: str, orders: list, ip_hash: str = "", scenario_id: str = "",
) -> Customer:
    name = validate_name(name)
    specs = normalize_orders(orders)
    now = datetime.now(timezone.utc)

    product_ids = [s["product_id"] for s in specs]
    result = await db.execute(select(Product).where(Product.id.in_(product_ids)))
    products = {p.id: p for p in result.scalars().all()}
    missing = [pid for pid in product_ids if pid not in products]
    if missing:
        raise DemoValidationError(f"unknown product_id: {missing[0]}")

    customer = Customer(
        id=str(uuid.uuid4()),
        name=name,
        email=demo_email(name),
        metadata_={
            "demo": True,
            "created_at": now.isoformat(),
            "ip_hash": ip_hash,
            "scenario_id": scenario_id,
        },
    )
    db.add(customer)
    await db.flush()

    for spec in specs:
        product = products[spec["product_id"]]
        created_at, delivered_at = order_dates(spec["ordered_relative"], spec["status"], now)
        order = Order(
            id=str(uuid.uuid4()),
            customer_id=customer.id,
            status=spec["status"],
            total_amount=product.price,
            created_at=created_at,
            updated_at=created_at,
            delivered_at=delivered_at,
        )
        db.add(order)
        await db.flush()
        db.add(OrderItem(
            id=str(uuid.uuid4()),
            order_id=order.id,
            product_id=product.id,
            quantity=1,
            price_at_purchase=product.price,
        ))
        if spec["status"] == "returned":
            # A returned order without a refund row makes get_refund_status
            # say "no refunds" — not what a returned order looks like.
            db.add(Refund(
                id=str(uuid.uuid4()),
                order_id=order.id,
                customer_id=customer.id,
                amount=product.price,
                reason="changed_mind",
                status="processed",
                initiated_by="customer",
                created_at=(delivered_at or created_at) + timedelta(days=3),
                processed_at=(delivered_at or created_at) + timedelta(days=6),
            ))
    await db.commit()
    return customer


async def load_customer(db: AsyncSession, customer_id: str) -> "Customer | None":
    result = await db.execute(
        select(Customer)
        .options(selectinload(Customer.orders).selectinload(Order.items).selectinload(OrderItem.product))
        .where(Customer.id == customer_id)
    )
    return result.scalar_one_or_none()


def _relative_label(created_at: datetime, now: datetime) -> str:
    days = max(0, (now - created_at).days)
    if days < 1:
        return "today"
    if days < 7:
        return f"{days} day{'s' if days != 1 else ''} ago"
    if days < 30:
        w = days // 7
        return f"{w} week{'s' if w != 1 else ''} ago"
    if days < 365:
        m = days // 30
        return f"{m} month{'s' if m != 1 else ''} ago"
    return "over a year ago"


def serialize_customer(customer: Customer) -> dict:
    now = datetime.now(timezone.utc)
    meta = customer.metadata_ or {}
    orders = sorted(customer.orders, key=lambda o: o.created_at or now, reverse=True)
    return {
        "customer_id": customer.id,
        "name": customer.name,
        # Never rendered by the UI; returned so the PII check can exclude the
        # customer's own address (the agent may legitimately mention it).
        "email": customer.email,
        "scenario_id": meta.get("scenario_id", ""),
        "demo": bool(meta.get("demo")),
        "orders": [
            {
                "order_id": o.id,
                "product_name": ", ".join(i.product.name for i in o.items) if o.items else "",
                "category": o.items[0].product.category if o.items else "",
                "status": o.status,
                "order_date": (o.created_at.isoformat() if o.created_at else None),
                "ordered_label": _relative_label(o.created_at, now) if o.created_at else "",
            }
            for o in orders
        ],
    }
