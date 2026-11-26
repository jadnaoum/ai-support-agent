"""Export a real conversation (UI test, demo, production) as a draft eval case.

    python -m scripts.export_conversation_case <conversation_id>
    python -m scripts.export_conversation_case <conversation_id> --upto 3   # first 3 customer turns

Prints JSON with the fields an eval row needs:
  - conversation: the messages as [{"role": "user"|"assistant", "content": ...}]
  - mock_account_state: the customer's actual orders as an eval fixture —
    product names, categories, warranty / final-sale / return-window, and
    dates as $EVAL_DATE-Nd placeholders relative to when the conversation
    happened, so the fixture keeps the same age relationships forever
    (see .claude/rules/eval-conventions.md)
  - notes: where the case came from
  - expected_behavior / judge_rubric: TODO — the case author writes these

Agent turns are exported as they happened. For a scripted multi-turn case,
replace earlier agent turns that were wrong with the reply the agent SHOULD
have given, so the final turn tests what it's meant to test.

Habit (eval-discipline): every UI-test or demo break becomes a regression
case. This script makes that a two-minute job.
"""
import argparse
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select

from backend.db.models import Conversation, Customer, Message, Order, OrderItem, Product
from backend.db.session import AsyncSessionLocal


def _relative(dt: "datetime | None", anchor: datetime) -> "str | None":
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (anchor - dt).days
    return f"$EVAL_DATE-{days}d" if days >= 0 else f"$EVAL_DATE+{-days}d"


async def export(conversation_id: str, upto: "int | None") -> dict:
    async with AsyncSessionLocal() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None:
            raise SystemExit(f"Conversation {conversation_id} not found.")
        anchor = conv.started_at or datetime.now(timezone.utc)
        customer = await db.get(Customer, conv.customer_id)

        msgs = (await db.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
        )).scalars().all()
        conversation, customer_turns = [], 0
        for m in msgs:
            if m.role not in ("customer", "agent"):
                continue
            if m.role == "customer":
                customer_turns += 1
                if upto and customer_turns > upto:
                    break
            conversation.append({"role": "user" if m.role == "customer" else "assistant", "content": m.content})
        # A case ends on a customer turn — that's the turn the agent is judged on.
        while conversation and conversation[-1]["role"] == "assistant":
            conversation.pop()

        orders = (await db.execute(
            select(Order).where(Order.customer_id == conv.customer_id).order_by(Order.created_at.desc())
        )).scalars().all()
        fixture_orders = []
        for o in orders:
            rows = (await db.execute(
                select(OrderItem, Product).join(Product, OrderItem.product_id == Product.id)
                .where(OrderItem.order_id == o.id)
            )).all()
            items = []
            for item, product in rows:
                entry = {"name": product.name, "qty": item.quantity, "price": float(item.price_at_purchase),
                         "category": product.category, "return_window_days": product.return_window_days}
                if product.warranty_months:
                    entry["warranty_months"] = product.warranty_months
                if product.final_sale:
                    entry["final_sale"] = True
                items.append(entry)
            fixture = {"id": str(o.id), "status": o.status, "total": float(o.total_amount),
                       "placed_at": _relative(o.created_at, anchor), "items": items}
            if o.delivered_at:
                fixture["delivered_date"] = _relative(o.delivered_at, anchor)
            fixture_orders.append(fixture)

    return {
        "source_conversation_id": conversation_id,
        "conversation": conversation,
        "mock_account_state": {"customer_id": "C-001", "name": customer.name if customer else "Test Customer",
                               "orders": fixture_orders},
        "notes": f"Exported from conversation {conversation_id} ({anchor.date().isoformat()}).",
        "expected_behavior": "TODO",
        "judge_rubric": "TODO",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("conversation_id")
    parser.add_argument("--upto", type=int, default=None,
                        help="keep only the first N customer turns (judge the agent on turn N)")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(export(args.conversation_id, args.upto)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
