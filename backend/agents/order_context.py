"""
Render the customer's recent orders (customer_context["recent_orders"]) as the
text block the response agent and the output guard both see (one renderer, so
the guard verifies exactly what the agent was shown). The intent classifier
gets the identity-only variant; the loop-decision step gets no list at all.
"""
from datetime import datetime, timezone


def today_line(now: "datetime | None" = None) -> str:
    """Today's date for the reply step and output guard, so relative dates
    ("delivered yesterday", "3 days ago") can be checked against tool dates.
    Same clock as the tools' eligibility checks (UTC)."""
    now = now or datetime.now(timezone.utc)
    return f"Today's date: {now:%Y-%m-%d} ({now:%A})"


def _order_line(order: dict, identity_only: bool) -> str:
    placed = (order.get("placed_at") or "")[:10] or "unknown date"
    items = ", ".join(
        f"{i.get('product', 'item')} (x{i.get('quantity', 1)})"
        for i in order.get("items") or []
    ) or "no items listed"
    if identity_only:
        return f"- {order.get('order_id', '')} | placed {placed} | {items}"
    total = order.get("total")
    total_text = f"${total:.2f}" if isinstance(total, (int, float)) else "unknown total"
    return (
        f"- {order.get('order_id', '')} | {order.get('status', '')} | "
        f"placed {placed} | {total_text} | {items}"
    )


def format_recent_orders(customer_context: "dict | None", identity_only: bool = False) -> str:
    """Return the order block, or "" when the order list wasn't loaded.

    A missing "recent_orders" key means the customer lookup failed or wasn't
    run — the block is omitted so the agent never claims the account has no
    orders. An empty list is a real "no orders" and is said explicitly.

    identity_only=True (the intent classifier) drops status and total: with
    them visible, the classifier matched "return my order" to the only
    delivered order — an eligibility judgment that belongs to the tools.
    """
    ctx = customer_context or {}
    if "recent_orders" not in ctx:
        return ""
    orders = ctx.get("recent_orders") or []
    if not orders:
        return "Recent orders: none on this account"
    return "Recent orders (newest first):\n" + "\n".join(_order_line(o, identity_only) for o in orders)
