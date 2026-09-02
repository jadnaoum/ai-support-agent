"""Business rules as one-liners for the demo's "rules" panel.

Nothing here is hand-typed policy: every line is derived from the product
table or from the constants and eligibility helpers the tools actually run,
so a changed product row or constant changes the panel.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Product, Customer
from backend.demo.scenarios import ORDER_STATUSES
from backend.tools.constants import (
    RETURN_REVIEW_THRESHOLD_EUR, ESCALATION_REASONS, CONFIRMATION_GATED_TOOLS,
)
from backend.tools.order_tools import _check_cancel_eligibility_data, _NON_RETURNABLE_CATEGORIES


# Customer-facing nouns for the confirmation-gated write tools.
_GATED_TOOL_NOUNS: dict = {"cancel_order": "cancellation", "initiate_return": "return"}


def _humanize(s: str) -> str:
    return s.replace("_", " ")


def _join(items: list) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def global_rules(products: list) -> list:
    """Rules that apply to every account. ``products`` is the full catalog."""
    rules = []

    # Return windows by category — derived from product rows.
    windows: dict = {}
    for p in products:
        windows.setdefault(p.category, set()).add(p.return_window_days)
    if windows:
        by_days: dict = {}
        for cat, days in windows.items():
            for d in days:
                by_days.setdefault(d, []).append(_humanize(cat))
        parts = [f"{_join(sorted(cats))}: {d} days" for d, cats in sorted(by_days.items())]
        rules.append({"text": "Return window · " + " · ".join(parts), "source": "products"})

    # Cancellation — derived by evaluating the real eligibility helper.
    cancellable = [s for s in ORDER_STATUSES if _check_cancel_eligibility_data(s)["eligible"]]
    blocked = [s for s in ORDER_STATUSES if not _check_cancel_eligibility_data(s)["eligible"]]
    rules.append({
        "text": f"Orders can be cancelled while {_join(cancellable)}, not once {_join(blocked)}",
        "source": "order_tools._check_cancel_eligibility_data",
    })

    gated = [_GATED_TOOL_NOUNS.get(t, _humanize(t)) for t in CONFIRMATION_GATED_TOOLS]
    rules.append({
        "text": f"Any {' or '.join(gated)} needs your confirmation before it runs",
        "source": "constants.CONFIRMATION_GATED_TOOLS",
    })
    rules.append({
        "text": f"Returns on orders over €{RETURN_REVIEW_THRESHOLD_EUR:.0f} go to human review before a label is issued",
        "source": "constants.RETURN_REVIEW_THRESHOLD_EUR",
    })
    rules.append({
        "text": f"{_join([_humanize(r) for r in sorted(ESCALATION_REASONS)]).capitalize()} claims are handed to a human",
        "source": "constants.ESCALATION_REASONS",
    })
    final_sale = [p.name for p in products if p.final_sale]
    if final_sale:
        rules.append({"text": f"Final sale, no returns: {_join(sorted(final_sale))}", "source": "products.final_sale"})
    if _NON_RETURNABLE_CATEGORIES:
        rules.append({
            "text": f"Never returnable: {_join([_humanize(c) for c in sorted(_NON_RETURNABLE_CATEGORIES)])} items",
            "source": "order_tools._NON_RETURNABLE_CATEGORIES",
        })
    return rules


def account_rules(customer: Customer) -> list:
    """Per-product rules for the products on this customer's orders."""
    seen = set()
    rules = []
    for o in customer.orders:
        for item in o.items:
            p = item.product
            if p.id in seen:
                continue
            seen.add(p.id)
            bits = [f"{p.return_window_days}-day return window"]
            if p.warranty_months:
                bits.append(f"{p.warranty_months}-month warranty")
            else:
                bits.append("no warranty")
            if p.final_sale:
                bits.append("final sale, no returns")
            rules.append({"text": f"{p.name} · " + " · ".join(bits), "source": "products", "product_id": p.id})
    return rules


async def rules_for(db: AsyncSession, customer: "Customer | None") -> dict:
    result = await db.execute(select(Product).order_by(Product.name))
    products = result.scalars().all()
    return {
        "account": account_rules(customer) if customer else [],
        "global": global_rules(products),
    }
