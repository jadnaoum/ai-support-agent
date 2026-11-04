"""Purge demo customers (metadata.demo = true) older than a TTL.

    python -m scripts.purge_demo_customers            # default TTL from settings
    python -m scripts.purge_demo_customers --older-than-hours 48 --dry-run

Customers with metadata.pinned = true are kept (hand-picked replay links).
Intended to run nightly from a cron/scheduler — not wired up yet.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete

from backend.config import get_settings
from backend.db.session import AsyncSessionLocal
from backend.db.models import (
    Customer, Order, OrderItem, Refund, Conversation, Message, AuditLog, Escalation, Turn,
)


async def purge(older_than_hours: int, dry_run: bool) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Customer).where(
                Customer.metadata_["demo"].as_boolean().is_(True),
                Customer.created_at < cutoff,
            )
        )
        customers = [c for c in result.scalars().all() if not (c.metadata_ or {}).get("pinned")]
        if not customers:
            print("Nothing to purge.")
            return 0
        ids = [c.id for c in customers]
        print(f"{'Would purge' if dry_run else 'Purging'} {len(ids)} demo customer(s) created before {cutoff.isoformat()}")
        if dry_run:
            return len(ids)

        conv_ids = [r[0] for r in (await db.execute(
            select(Conversation.id).where(Conversation.customer_id.in_(ids)))).all()]
        order_ids = [r[0] for r in (await db.execute(
            select(Order.id).where(Order.customer_id.in_(ids)))).all()]

        # FK order: children first.
        await db.execute(delete(Refund).where(Refund.customer_id.in_(ids)))
        if order_ids:
            await db.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
        await db.execute(delete(Order).where(Order.customer_id.in_(ids)))
        if conv_ids:
            await db.execute(delete(Turn).where(Turn.conversation_id.in_(conv_ids)))
            await db.execute(delete(AuditLog).where(AuditLog.conversation_id.in_(conv_ids)))
            await db.execute(delete(Escalation).where(Escalation.conversation_id.in_(conv_ids)))
            await db.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
        await db.execute(delete(Conversation).where(Conversation.customer_id.in_(ids)))
        await db.execute(delete(Customer).where(Customer.id.in_(ids)))
        await db.commit()
        print("Done.")
        return len(ids)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--older-than-hours", type=int, default=get_settings().demo_customer_ttl_hours)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(purge(args.older_than_hours, args.dry_run))


if __name__ == "__main__":
    main()
