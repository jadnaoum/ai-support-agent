"""
Mock tool resolver for eval mode.

Called by action_service when config["configurable"]["mock_account_state"] is set.
Returns deterministic tool responses from mock order data without touching the database.
Production code never imports this module.

Tools covered: track_order, check_cancel_eligibility, check_return_eligibility,
check_missing_package, cancel_order, initiate_return, get_refund_status.
"""
import uuid
from datetime import datetime, timezone

from backend.tools.order_tools import (
    _has_prior_confirmation, _REASON_MAP, REASON_VALUES,
    _escalation_rejection, ESCALATION_REASONS,
)

_DEFAULT_RETURN_WINDOW_DAYS = 30
_ELECTRONICS_RETURN_WINDOW_DAYS = 14
_DEFAULT_WARRANTY_MONTHS = 12


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value) -> "datetime | None":
    """Parse a date/datetime string from mock data to a UTC-aware datetime."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _delivery_date(o: dict) -> "datetime | None":
    """Extract the best available date from a mock order dict."""
    return _parse_date(
        o.get("delivered_date") or o.get("placed_at") or o.get("placed_date")
    )


def _get_order(mock: dict, order_id) -> "tuple[dict | None, dict | None]":
    """
    Look up an order from mock data.
    Returns (order_dict, error_result) — exactly one will be None.
    If order_id is None, returns the first order in the list.
    """
    orders = mock.get("orders", [])
    if order_id:
        o = next((o for o in orders if str(o.get("id", "")) == str(order_id)), None)
        if o is None:
            return None, {"success": False, "error": f"Order {order_id} not found."}
        return o, None
    if not orders:
        return None, {"success": False, "error": "No orders found for this account."}
    return orders[0], None


def _item_names(o: dict) -> list:
    """Extract item name list from mock order dict."""
    if o.get("items"):
        return [i.get("name", "") for i in o["items"]]
    if o.get("item"):
        return [o["item"]]
    return []


def _order_meta(o: dict, now: datetime) -> dict:
    """Extract human-readable context fields from a mock order for inclusion in tool results.

    Surfaces the intermediate values the tool already computes so the agent
    never has to interpret an enum value to understand the outcome.
    """
    items = o.get("items") or []
    first_item = items[0] if items else {}
    product_name = first_item.get("name") or o.get("item") or "Unknown item"
    category = first_item.get("category", "unknown")

    dt = _delivery_date(o)
    days_since = (now - dt).days if dt else None
    return_window = _order_return_window_days(o)

    return {
        "product_name": product_name,
        "category": category,
        "days_since_delivery": days_since,
        "return_window_days": return_window,
        "warranty_months": first_item.get("warranty_months") or _DEFAULT_WARRANTY_MONTHS,
    }


# ---------------------------------------------------------------------------
# Per-status eligibility helpers (inline — avoids ORM dependency)
# ---------------------------------------------------------------------------

def _cancel_eligibility(status: str) -> dict:
    if status == "cancelled":
        return {"eligible": False, "reason": "already_cancelled", "available_action": None,
                "detail": "Order is already cancelled."}
    if status == "refunded":
        return {"eligible": False, "reason": "refunded", "available_action": None,
                "detail": "Order has already been refunded."}
    if status == "returned":
        return {"eligible": False, "reason": "returned", "available_action": None,
                "detail": "Order has already been returned."}
    if status == "shipped":
        return {"eligible": False, "reason": "shipped", "available_action": None,
                "next_step": "Customer can return after delivery",
                "detail": "Order has shipped — cancellation not possible. Return available after delivery."}
    if status == "delivered":
        return {"eligible": False, "reason": "delivered", "available_action": "initiate_return",
                "detail": "Order delivered — cancellation not possible. Return/refund available instead."}
    return {"eligible": True, "reason": None, "available_action": None,
            "detail": f"Order is eligible for cancellation (status: {status})."}


def _order_return_window_days(o: dict) -> int:
    """Return the applicable return window for an order based on item categories."""
    items = o.get("items") or []
    if any(i.get("category", "").lower() == "electronics" for i in items):
        return _ELECTRONICS_RETURN_WINDOW_DAYS
    return _DEFAULT_RETURN_WINDOW_DAYS


def _defective_reason(o: dict, now: datetime) -> str:
    """Return the granular defective reason based on delivery date vs windows."""
    dt = _delivery_date(o)
    if not dt:
        return "defective_within_return_window"
    days_since = (now - dt).days
    return_window = _order_return_window_days(o)
    if days_since <= return_window:
        return "defective_within_return_window"
    items = o.get("items") or []
    first_item = items[0] if items else {}
    warranty_months = first_item.get("warranty_months") or _DEFAULT_WARRANTY_MONTHS
    if days_since <= warranty_months * 30:
        return "defective_within_warranty"
    return "defective_no_coverage"


def _return_eligibility(o: dict, reason: str, now: datetime) -> dict:
    """Return eligibility for initiate_return / check_return_eligibility."""
    meta = _order_meta(o, now)
    days_since = meta["days_since_delivery"]
    return_window = meta["return_window_days"]
    warranty_months = meta["warranty_months"]
    product_name = meta["product_name"]
    category = meta["category"]

    if reason and reason.lower() == "duplicate_item":
        return {
            "eligible": False,
            "reason": "duplicate_item",
            **meta,
            "detail": "Duplicate item fulfillment error — requires human review.",
            "available_action": None,
            "check_kb": False,
            "requires_escalation": True,
        }

    if reason and reason.lower() in ("defective", "broken", "damaged"):
        defective_reason = _defective_reason(o, now)
        if defective_reason == "defective_within_return_window":
            detail = (
                f"Defective item within the {return_window}-day return window "
                f"({days_since} days since delivery). "
                f"Replacement or full refund available."
            )
        elif defective_reason == "defective_within_warranty":
            detail = (
                f"Outside the {return_window}-day return window "
                f"({days_since} days since delivery). "
                f"Within {warranty_months}-month warranty period. "
                f"Warranty claim applies."
            )
        else:
            detail = (
                f"Outside the {return_window}-day return window "
                f"({days_since} days since delivery) "
                f"and outside {warranty_months}-month warranty. No coverage."
            )
        return {
            "eligible": False,
            "reason": defective_reason,
            **meta,
            "detail": detail,
            "available_action": None,
            "check_kb": True,
            "requires_escalation": True,
        }

    status = o.get("status", "placed")
    if status == "return_in_progress":
        return {"eligible": False, "reason": "already_in_progress",
                **meta,
                "detail": f"A return is already in progress for {product_name}.",
                "available_action": None}
    if status == "returned":
        return {"eligible": False, "reason": "already_returned",
                **meta,
                "detail": f"{product_name} has already been returned.",
                "available_action": None}
    if status == "refunded":
        return {"eligible": False, "reason": "already_refunded",
                **meta,
                "detail": f"{product_name} has already been refunded.",
                "available_action": None}
    if status != "delivered":
        return {"eligible": False, "reason": "wrong_status",
                **meta,
                "detail": f"Cannot return — order status is '{status}', not delivered.",
                "available_action": None}

    # Return window check
    if days_since is not None and days_since > return_window:
        return {"eligible": False, "reason": "outside_return_window",
                **meta,
                "detail": (
                    f"Return window closed. {return_window}-day window expired "
                    f"({days_since} days since delivery)."
                ),
                "available_action": None}

    return {"eligible": True, "reason": None,
            **meta,
            "detail": (
                f"{product_name} ({category}) is eligible for return. "
                f"{days_since} days since delivery, within {return_window}-day window."
            ),
            "available_action": None,
            "check_kb": True}


# ---------------------------------------------------------------------------
# Mock tool implementations
# ---------------------------------------------------------------------------

def _mock_track_order(params: dict, mock: dict) -> dict:
    o, err = _get_order(mock, params.get("order_id"))
    if err:
        return err

    items = []
    if o.get("items"):
        for i in o["items"]:
            items.append({"product": i.get("name", ""), "quantity": i.get("qty", 1),
                          "price": float(i.get("price", 0.0))})
    elif o.get("item"):
        items = [{"product": o["item"], "quantity": 1, "price": float(o.get("total", 0.0))}]

    status = o.get("status", "placed")
    product_names = ", ".join(i["product"] for i in items) or "Unknown item"
    detail = f"{product_names} — status: {status}."
    if o.get("delivered_date"):
        detail += f" Delivered {o['delivered_date']}."
    elif o.get("eta"):
        detail += f" Estimated delivery: {o['eta']}."

    result = {
        "success": True,
        "order_id": str(o.get("id", "")),
        "status": status,
        "total": float(o.get("total", 0.0)),
        "placed_at": o.get("placed_at") or o.get("placed_date"),
        "items": items,
        "detail": detail,
    }
    if o.get("tracking"):
        result["tracking_number"] = o["tracking"]
    if o.get("eta"):
        result["estimated_delivery"] = o["eta"]
    if o.get("delivered_date"):
        result["delivered_at"] = o["delivered_date"]
    return result


def _mock_check_cancel_eligibility(params: dict, mock: dict) -> dict:
    order_id = params.get("order_id")
    if order_id:
        o, err = _get_order(mock, order_id)
        if err:
            return err
        check = _cancel_eligibility(o.get("status", "placed"))
        product_name = (_item_names(o) or ["Unknown item"])[0]
        return {"success": True, "order_id": str(o["id"]),
                "product_name": product_name,
                "order_status": o.get("status", "placed"),
                **check}

    orders = mock.get("orders", [])
    if not orders:
        return {"success": False, "error": "No orders found for this account."}
    eligible = []
    for o in orders:
        check = _cancel_eligibility(o.get("status", "placed"))
        if check["eligible"]:
            product_name = (_item_names(o) or ["Unknown item"])[0]
            eligible.append({"order_id": str(o["id"]),
                             "product_name": product_name,
                             "order_status": o.get("status"), **check})
    return {"success": True, "eligible_orders": eligible}


def _mock_check_return_eligibility(params: dict, mock: dict, now: datetime) -> dict:
    order_id = params.get("order_id")
    reason = params.get("reason")
    if order_id:
        o, err = _get_order(mock, order_id)
        if err:
            return err
        check = _return_eligibility(o, reason, now)
        return {"success": True, "order_id": str(o["id"]), **check}

    orders = mock.get("orders", [])
    if not orders:
        return {"success": False, "error": "No orders found for this account."}
    eligible = []
    escalation = None
    for o in orders:
        check = _return_eligibility(o, reason, now)
        if check["eligible"]:
            eligible.append({"order_id": str(o["id"]), "status": o.get("status"), **check})
        elif check.get("check_kb"):
            escalation = {"order_id": str(o["id"]), "status": o.get("status"), **check}
    if escalation and not eligible:
        return {"success": True, **escalation}
    return {"success": True, "eligible_orders": eligible}


def _mock_cancel_order(params: dict, mock: dict, actions_taken: list, now: datetime) -> dict:
    if not params.get("order_id"):
        return {
            "success": False,
            "reason": "order_id_required",
            "available_action": "check_cancel_eligibility",
        }
    o, err = _get_order(mock, params.get("order_id"))
    if err:
        return err
    oid = str(o["id"])

    check = _cancel_eligibility(o.get("status", "placed"))
    if not check["eligible"]:
        product_name = (_item_names(o) or ["Unknown item"])[0]
        result = {"success": False, "reason": check["reason"],
                  "product_name": product_name,
                  "order_status": o.get("status", "placed"),
                  "detail": check["detail"],
                  "available_action": check["available_action"]}
        if check.get("next_step"):
            result["next_step"] = check["next_step"]
        return result

    reason = params.get("reason")
    if not reason:
        return {"success": False, "reason": "reason_required"}
    if reason not in _REASON_MAP:
        return {"success": False, "error": "invalid_reason",
                "message": f"Reason must be one of: {REASON_VALUES}. Received: {reason}"}

    resolved_reason = _REASON_MAP[reason]
    if resolved_reason in ESCALATION_REASONS:
        return _escalation_rejection(resolved_reason)

    if not _has_prior_confirmation(actions_taken, "cancel_order", oid):
        return {"success": False, "confirmation_required": True,
                "details": {"order_id": oid, "order_total": float(o.get("total", 0.0)),
                            "items": _item_names(o)}}

    refund_amount = float(o.get("total", 0.0))
    product_name = (_item_names(o) or ["Unknown item"])[0]
    return {"success": True, "order_id": oid,
            "product_name": product_name,
            "refund_id": "mock-refund-id",
            "refund_amount": refund_amount,
            "detail": f"Order cancelled. Refund of ${refund_amount:.2f} issued to original payment method.",
            "message": f"Order cancelled successfully. A refund of ${refund_amount:.2f} "
                       "has been issued to your original payment method."}


def _mock_initiate_return(params: dict, mock: dict, actions_taken: list, now: datetime) -> dict:
    if not params.get("order_id"):
        return {
            "success": False,
            "reason": "order_id_required",
            "available_action": "check_return_eligibility",
        }
    o, err = _get_order(mock, params.get("order_id"))
    if err:
        return err
    oid = str(o["id"])
    reason = params.get("reason")

    check = _return_eligibility(o, reason, now)
    if not check["eligible"]:
        result = {"success": False, "reason": check["reason"],
                  "detail": check.get("detail", ""),
                  "available_action": check["available_action"]}
        # Pass through all meta fields so the agent has full context on ineligibility
        for k in ("product_name", "category", "days_since_delivery",
                  "return_window_days", "warranty_months"):
            if k in check:
                result[k] = check[k]
        if check.get("requires_escalation"):
            result["requires_escalation"] = True
        if check.get("check_kb"):
            result["check_kb"] = True
        return result

    if not reason:
        return {"success": False, "reason": "reason_required"}
    if reason not in _REASON_MAP:
        return {"success": False, "error": "invalid_reason",
                "message": f"Reason must be one of: {REASON_VALUES}. Received: {reason}"}

    if not _has_prior_confirmation(actions_taken, "initiate_return", oid):
        return {"success": False, "confirmation_required": True,
                "details": {"order_id": oid, "items": _item_names(o), "reason": reason}}

    total = float(o.get("total", 0.0))
    product_name = (_item_names(o) or ["Unknown item"])[0]
    if total > 50:
        return {"success": True, "order_id": oid,
                "product_name": product_name,
                "refund_id": "mock-refund-id",
                "pending_review": True,
                "detail": f"Return for {product_name} initiated. Order total ${total:.2f} requires manual review before refund is issued."}

    label_id = f"RETURN-{str(uuid.uuid4())[:8].upper()}"
    return {"success": True, "order_id": oid,
            "product_name": product_name,
            "return_label": label_id,
            "refund_id": "mock-refund-id",
            "detail": f"Return initiated for {product_name}. Prepaid label {label_id} emailed.",
            "message": f"Return initiated. A prepaid return label ({label_id}) has been "
                       "emailed to you. Once we receive your item, your refund will be processed."}


def _mock_check_missing_package(params: dict, mock: dict, now: datetime) -> dict:
    o, err = _get_order(mock, params.get("order_id"))
    if err:
        return err

    status = o.get("status", "placed")
    if status != "delivered":
        return {
            "success": False,
            "error": f"Order status is '{status}', not delivered. Missing package claims require a delivered order.",
        }

    items = o.get("items") or []
    first_item = items[0] if items else {}
    product_name = first_item.get("name") or o.get("item") or "Unknown item"
    oid = str(o.get("id", ""))

    dt = _delivery_date(o)
    if dt:
        delta = now - dt
        total_days = delta.days
        full_weeks, remainder = divmod(total_days, 7)
        business_days = full_weeks * 5
        start_weekday = dt.weekday()
        for i in range(remainder):
            if (start_weekday + i) % 7 < 5:
                business_days += 1
        delivered_date_str = dt.strftime("%Y-%m-%d")
    else:
        business_days = 1
        delivered_date_str = "unknown"

    if business_days < 1:
        return {
            "success": True,
            "order_id": oid,
            "product_name": product_name,
            "delivered_date": delivered_date_str,
            "business_days_since_delivery": business_days,
            "status": "wait",
            "requires_escalation": False,
            "check_kb": True,
            "detail": (
                f"Order delivered {delivered_date_str}, less than 1 business day ago. "
                "Carriers sometimes mark packages as delivered early. "
                "Customer should wait one additional business day."
            ),
        }

    return {
        "success": True,
        "order_id": oid,
        "product_name": product_name,
        "delivered_date": delivered_date_str,
        "business_days_since_delivery": business_days,
        "status": "file_claim",
        "requires_escalation": True,
        "check_kb": True,
        "detail": (
            f"Order delivered {delivered_date_str}, {business_days} business day(s) ago. "
            "Eligible for carrier investigation — reship or full refund. "
            "Requires specialist to file carrier claim."
        ),
    }


def _mock_get_refund_status(params: dict, mock: dict) -> dict:
    order_id = params.get("order_id")
    refunds = mock.get("refunds", [])
    if order_id:
        o, err = _get_order(mock, order_id)
        if err:
            return err
        refunds = [r for r in refunds if str(r.get("order_id", "")) == str(order_id)]
    if not refunds:
        return {"success": True, "refunds": [], "check_kb": True,
                "detail": "No refunds found for this account."}

    refund_list = []
    for r in refunds:
        amount = float(r.get("amount", 0.0))
        status = r.get("status", "approved")
        detail = f"Refund of ${amount:.2f} — status: {status}."
        if r.get("created_at"):
            detail += f" Requested {r['created_at']}."
        refund_list.append({
            "refund_id": str(r.get("refund_id", "mock-refund-id")),
            "order_id": str(r.get("order_id", "")),
            "amount": amount,
            "status": status,
            "reason": r.get("reason", "other"),
            "created_at": r.get("created_at"),
            "detail": detail,
        })

    return {
        "success": True,
        "check_kb": True,
        "refunds": refund_list,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def mock_tool_call(tool_name: str, params: dict, mock: dict,
                   customer_id: str, actions_taken: list) -> dict:
    """
    Entry point called by action_service when mock_account_state is present.
    Dispatches to the appropriate mock implementation.
    """
    now = datetime.now(timezone.utc)

    dispatch = {
        "track_order":               lambda: _mock_track_order(params, mock),
        "check_cancel_eligibility":  lambda: _mock_check_cancel_eligibility(params, mock),
        "check_return_eligibility":  lambda: _mock_check_return_eligibility(params, mock, now),
        "check_missing_package":     lambda: _mock_check_missing_package(params, mock, now),
        "cancel_order":              lambda: _mock_cancel_order(params, mock, actions_taken, now),
        "initiate_return":           lambda: _mock_initiate_return(params, mock, actions_taken, now),
        "get_refund_status":         lambda: _mock_get_refund_status(params, mock),
    }

    fn = dispatch.get(tool_name)
    if fn is None:
        return {"success": False, "error": f"No mock handler for tool '{tool_name}'."}
    return fn()
