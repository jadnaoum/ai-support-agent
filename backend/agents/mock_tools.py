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


def _order_return_window_days(o: dict) -> int:
    """Return the applicable return window for an order based on item categories."""
    items = o.get("items") or []
    if any(i.get("category", "").lower() == "electronics" for i in items):
        return _ELECTRONICS_RETURN_WINDOW_DAYS
    return _DEFAULT_RETURN_WINDOW_DAYS


# ---------------------------------------------------------------------------
# Per-status eligibility helpers (inline — avoids ORM dependency)
# ---------------------------------------------------------------------------

def _cancel_eligibility(status: str) -> dict:
    if status == "cancelled":
        return {"eligible": False, "reason": "already_cancelled", "available_action": None}
    if status == "refunded":
        return {"eligible": False, "reason": "already_refunded", "available_action": None}
    if status == "returned":
        return {"eligible": False, "reason": "already_returned", "available_action": None}
    if status == "shipped":
        return {"eligible": False, "reason": "already_shipped", "available_action": None}
    if status == "delivered":
        return {"eligible": False, "reason": "already_delivered", "available_action": "check_return_eligibility"}
    return {"eligible": True, "reason": "eligible", "available_action": None}


def _return_eligibility(o: dict, reason: str, now: datetime) -> dict:
    """Return eligibility for initiate_return / check_return_eligibility."""
    if reason and reason.lower() == "duplicate_item":
        return {"eligible": False, **_escalation_rejection("duplicate_item")}

    if reason and reason.lower() in ("defective", "broken", "damaged"):
        in_return_window = False
        in_warranty = False
        warranty_months = None
        dt = _delivery_date(o)
        if dt:
            days_since = (now - dt).days
            return_window = _order_return_window_days(o)
            in_return_window = days_since <= return_window
            items = o.get("items") or []
            first_item = items[0] if items else {}
            wm = first_item.get("warranty_months") or _DEFAULT_WARRANTY_MONTHS
            in_warranty = days_since <= wm * 30
            warranty_months = wm
        return {
            "eligible": False,
            **_escalation_rejection(
                "defective",
                in_return_window=in_return_window,
                in_warranty=in_warranty,
                warranty_months=warranty_months,
            ),
        }

    status = o.get("status", "placed")
    if status == "return_in_progress":
        return {"eligible": False, "reason": "already_in_progress", "available_action": None}
    if status == "returned":
        return {"eligible": False, "reason": "already_returned", "available_action": None}
    if status == "refunded":
        return {"eligible": False, "reason": "already_refunded", "available_action": None}
    if status != "delivered":
        return {"eligible": False, "reason": "wrong_status", "available_action": None}

    dt = _delivery_date(o)
    if dt:
        days_since = (now - dt).days
        return_window = _order_return_window_days(o)
        if days_since > return_window:
            return {"eligible": False, "reason": "outside_return_window", "available_action": None}

    return {"eligible": True, "reason": "eligible", "available_action": None}


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

    result = {
        "success": True,
        "reason": "tracking_info",
        "order_id": str(o.get("id", "")),
        "status": o.get("status", "placed"),
        "total": float(o.get("total", 0.0)),
        "placed_at": o.get("placed_at") or o.get("placed_date"),
        "items": items,
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
        return {"success": True, "order_id": str(o["id"]), **check}

    orders = mock.get("orders", [])
    if not orders:
        return {"success": False, "error": "No orders found for this account."}
    eligible = []
    for o in orders:
        check = _cancel_eligibility(o.get("status", "placed"))
        if check["eligible"]:
            eligible.append({"order_id": str(o["id"]), "status": o.get("status"), **check})
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
        elif check.get("requires_escalation") and escalation is None:
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
        # Spreads every eligibility field (except `eligible`) into the write
        # tool's result. Any new field added to _cancel_eligibility will
        # automatically flow through to the agent via action_results.
        # If you add a field that should NOT reach the agent, filter it here.
        return {"success": False, **{k: v for k, v in check.items() if k != "eligible"}}

    reason = params.get("reason")
    if not reason:
        return {"success": False, "reason": "reason_required"}
    if reason not in _REASON_MAP:
        return {"success": False, "reason": "invalid_reason", "available_action": None}

    resolved_reason = _REASON_MAP[reason]
    if resolved_reason in ESCALATION_REASONS:
        return {"success": False, **_escalation_rejection(resolved_reason)}

    if not _has_prior_confirmation(actions_taken, "cancel_order", oid):
        return {
            "success": False,
            "reason": "confirmation_required",
            "confirmation_required": True,
            "details": {"order_id": oid, "order_total": float(o.get("total", 0.0)),
                        "items": _item_names(o)},
        }

    refund_amount = float(o.get("total", 0.0))
    return {
        "success": True,
        "reason": "cancelled",
        "order_id": oid,
        "refund_id": "mock-refund-id",
        "refund_amount": refund_amount,
    }


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
        # Spreads every eligibility field (except `eligible`) into the write
        # tool's result. Any new field added to _return_eligibility will
        # automatically flow through to the agent via action_results.
        # If you add a field that should NOT reach the agent, filter it here.
        return {"success": False, **{k: v for k, v in check.items() if k != "eligible"}}

    if not reason:
        return {"success": False, "reason": "reason_required"}
    if reason not in _REASON_MAP:
        return {"success": False, "reason": "invalid_reason", "available_action": None}

    if not _has_prior_confirmation(actions_taken, "initiate_return", oid):
        return {
            "success": False,
            "reason": "confirmation_required",
            "confirmation_required": True,
            "details": {"order_id": oid, "items": _item_names(o), "reason": reason},
        }

    total = float(o.get("total", 0.0))
    if total > 50:
        return {
            "success": True,
            "reason": "return_pending_review",
            "order_id": oid,
            "refund_id": "mock-refund-id",
            "refund_amount": total,
        }

    label_id = f"RETURN-{str(uuid.uuid4())[:8].upper()}"
    return {
        "success": True,
        "reason": "return_initiated",
        "order_id": oid,
        "return_label": label_id,
        "refund_id": "mock-refund-id",
        "refund_amount": total,
    }


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
            "reason": "wait_for_delivery",
            "order_id": oid,
            "product_name": product_name,
            "delivered_date": delivered_date_str,
            "business_days_since_delivery": business_days,
            "requires_escalation": False,
        }

    return {
        "success": True,
        "reason": "carrier_claim_eligible",
        "order_id": oid,
        "product_name": product_name,
        "delivered_date": delivered_date_str,
        "business_days_since_delivery": business_days,
        "requires_escalation": True,
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
        return {"success": True, "reason": "no_refunds", "refunds": []}

    refund_list = []
    for r in refunds:
        refund_list.append({
            "refund_id": str(r.get("refund_id", "mock-refund-id")),
            "order_id": str(r.get("order_id", "")),
            "amount": float(r.get("amount", 0.0)),
            "status": r.get("status", "approved"),
            "reason": r.get("reason", "other"),
            "created_at": r.get("created_at"),
        })

    return {
        "success": True,
        "reason": "refund_list",
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
