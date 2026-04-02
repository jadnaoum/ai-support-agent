"""
Mock tool resolver for eval mode.

Called by action_service when config["configurable"]["mock_account_state"] is set.
Returns deterministic tool responses from mock order data without touching the database.
Production code never imports this module.

Tools covered: track_order, check_cancel_eligibility, check_return_eligibility,
cancel_order, initiate_return, get_refund_status.
"""
import uuid
from datetime import datetime, timezone

from backend.tools.order_tools import _has_prior_confirmation, _REASON_MAP, REASON_VALUES

_DEFAULT_RETURN_WINDOW_DAYS = 30


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


# ---------------------------------------------------------------------------
# Per-status eligibility helpers (inline — avoids ORM dependency)
# ---------------------------------------------------------------------------

def _cancel_eligibility(status: str) -> dict:
    if status == "cancelled":
        return {"eligible": False, "reason": "already_cancelled",
                "details": "This order is already cancelled.", "available_action": None}
    if status == "refunded":
        return {"eligible": False, "reason": "refunded",
                "details": "This order has already been refunded.", "available_action": None}
    if status == "returned":
        return {"eligible": False, "reason": "returned",
                "details": "This order has already been returned and cannot be cancelled.",
                "available_action": None}
    if status == "shipped":
        return {"eligible": False, "reason": "shipped",
                "details": "This order has shipped and cannot be cancelled. "
                           "You can return it for a refund once it arrives.",
                "available_action": "check_return_eligibility"}
    if status == "delivered":
        return {"eligible": False, "reason": "delivered",
                "details": "Delivered orders cannot be cancelled. "
                           "Please request a return/refund instead.",
                "available_action": "initiate_return"}
    return {"eligible": True, "reason": None,
            "details": "This order can be cancelled.", "available_action": None}


def _return_eligibility(o: dict, reason: str, now: datetime) -> dict:
    """Return eligibility for initiate_return / check_return_eligibility."""
    if reason and reason.lower() in ("defective", "broken", "damaged"):
        return {"eligible": False, "reason": "requires_escalation",
                "details": "Defective and damaged item claims require review by our support team.",
                "available_action": None}
    status = o.get("status", "placed")
    if status == "return_in_progress":
        return {"eligible": False, "reason": "already_in_progress",
                "details": "A return has already been initiated for this order. "
                           "Check your email for the prepaid return label.",
                "available_action": None}
    if status == "returned":
        return {"eligible": False, "reason": "already_returned",
                "details": "This order has already been returned.", "available_action": None}
    if status == "refunded":
        return {"eligible": False, "reason": "already_refunded",
                "details": "This order has already been refunded.", "available_action": None}
    if status != "delivered":
        return {"eligible": False, "reason": "wrong_status",
                "details": f"Only delivered orders can be returned. "
                           f"This order has status '{status}'.",
                "available_action": None}
    # Return window check
    dt = _delivery_date(o)
    if dt:
        days_since = (now - dt).days
        if days_since > _DEFAULT_RETURN_WINDOW_DAYS:
            return {"eligible": False, "reason": "outside_return_window",
                    "details": f"The return window for this order has passed "
                               f"({_DEFAULT_RETURN_WINDOW_DAYS} days). "
                               "If your item is defective, please contact us — "
                               "defective items are handled separately.",
                    "available_action": None}
    return {"eligible": True, "reason": None,
            "details": "This order is eligible for a return.", "available_action": None,
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

    result = {
        "success": True,
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
    for o in orders:
        check = _return_eligibility(o, reason, now)
        if check["eligible"]:
            eligible.append({"order_id": str(o["id"]), "status": o.get("status"), **check})
    return {"success": True, "eligible_orders": eligible}


def _mock_cancel_order(params: dict, mock: dict, actions_taken: list, now: datetime) -> dict:
    o, err = _get_order(mock, params.get("order_id"))
    if err:
        return err
    oid = str(o["id"])

    check = _cancel_eligibility(o.get("status", "placed"))
    if not check["eligible"]:
        return {"success": False, "reason": check["reason"],
                "error": check["details"], "available_action": check["available_action"]}

    reason = params.get("reason")
    if not reason:
        return {"success": False, "reason": "reason_required",
                "details": f"Please provide a reason for the cancellation. "
                           f"Valid values: {', '.join(REASON_VALUES)}."}
    if reason not in _REASON_MAP:
        return {"success": False, "error": "invalid_reason",
                "message": f"Reason must be one of: {REASON_VALUES}. Received: {reason}"}

    if not _has_prior_confirmation(actions_taken, "cancel_order", oid):
        return {"success": False, "confirmation_required": True,
                "details": {"order_id": oid, "order_total": float(o.get("total", 0.0)),
                            "items": _item_names(o)}}

    refund_amount = float(o.get("total", 0.0))
    return {"success": True, "order_id": oid,
            "refund_id": "mock-refund-id",
            "refund_amount": refund_amount,
            "message": f"Order cancelled successfully. A refund of ${refund_amount:.2f} "
                       "has been issued to your original payment method."}


def _mock_initiate_return(params: dict, mock: dict, actions_taken: list, now: datetime) -> dict:
    o, err = _get_order(mock, params.get("order_id"))
    if err:
        return err
    oid = str(o["id"])
    reason = params.get("reason")

    check = _return_eligibility(o, reason, now)
    if not check["eligible"]:
        return {"success": False, "reason": check["reason"],
                "error": check["details"], "available_action": check["available_action"]}

    if not reason:
        return {"success": False, "reason": "reason_required",
                "details": f"Please provide a reason for the return. "
                           f"Valid values: {', '.join(REASON_VALUES)}."}
    if reason not in _REASON_MAP:
        return {"success": False, "error": "invalid_reason",
                "message": f"Reason must be one of: {REASON_VALUES}. Received: {reason}"}

    if not _has_prior_confirmation(actions_taken, "initiate_return", oid):
        return {"success": False, "confirmation_required": True,
                "details": {"order_id": oid, "items": _item_names(o), "reason": reason}}

    total = float(o.get("total", 0.0))
    if total > 50:
        return {"success": True, "order_id": oid,
                "refund_id": "mock-refund-id",
                "pending_review": True}

    label_id = f"RETURN-{str(uuid.uuid4())[:8].upper()}"
    return {"success": True, "order_id": oid, "return_label": label_id,
            "refund_id": "mock-refund-id",
            "message": f"Return initiated. A prepaid return label ({label_id}) has been "
                       "emailed to you. Once we receive your item, your refund will be processed."}


def _mock_get_refund_status(params: dict, mock: dict) -> dict:
    order_id = params.get("order_id")
    refunds = mock.get("refunds", [])
    if order_id:
        # Verify the order exists in mock data
        o, err = _get_order(mock, order_id)
        if err:
            return err
        refunds = [r for r in refunds if str(r.get("order_id", "")) == str(order_id)]
    if not refunds:
        return {"success": True, "refunds": [], "check_kb": True}
    return {
        "success": True,
        "check_kb": True,
        "refunds": [
            {
                "refund_id": str(r.get("refund_id", "mock-refund-id")),
                "order_id": str(r.get("order_id", "")),
                "amount": float(r.get("amount", 0.0)),
                "status": r.get("status", "approved"),
                "reason": r.get("reason", "other"),
                "created_at": r.get("created_at"),
            }
            for r in refunds
        ],
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
        "cancel_order":              lambda: _mock_cancel_order(params, mock, actions_taken, now),
        "initiate_return":           lambda: _mock_initiate_return(params, mock, actions_taken, now),
        "get_refund_status":         lambda: _mock_get_refund_status(params, mock),
    }

    fn = dispatch.get(tool_name)
    if fn is None:
        return {"success": False, "error": f"No mock handler for tool '{tool_name}'."}
    return fn()
