"""
Tool registry — defines all available actions, their parameters, and handlers.

All agent actions must go through this registry. Adding a new capability means
adding a ToolDefinition here — no hardcoded action logic in agents.
"""
from dataclasses import dataclass
from typing import Callable

from backend.tools.order_tools import (
    track_order,
    cancel_order,
    check_cancel_eligibility,
    check_return_eligibility,
    check_missing_package,
    initiate_return,
    get_refund_status,
)
from backend.tools.constants import REASON_VALUES

_REASON_LIST = ", ".join(REASON_VALUES)


@dataclass
class ToolDefinition:
    name: str
    # description was never injected into any LLM call (see .claude/rules/security.md)
    # and its yaml sources rotted; removed 2026-09-02. It returns as a REAL field at
    # the native tool-calling migration (PENDING #15), authored in prompts/ and passed
    # to the API tools parameter.
    parameters: dict          # {param_name: {"type": str, "required": bool, "description": str}}
    handler: Callable         # async (db, customer_id, **params) -> dict


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "track_order": ToolDefinition(
        name="track_order",
        parameters={
            "order_id": {"type": "str", "required": False, "description": "Order ID to look up. Omit to use most recent order."},
        },
        handler=track_order,
    ),
    "cancel_order": ToolDefinition(
        name="cancel_order",
        parameters={
            "order_id": {"type": "str", "required": False, "description": "Order ID to cancel. Omit to cancel most recent order."},
            "reason": {"type": "str", "required": True, "description": "Cancellation reason."},
        },
        handler=cancel_order,
    ),
    "check_cancel_eligibility": ToolDefinition(
        name="check_cancel_eligibility",
        parameters={
            "order_id": {"type": "str", "required": False, "description": "Order ID to check. Omit to check most recent order."},
        },
        handler=check_cancel_eligibility,
    ),
    "check_return_eligibility": ToolDefinition(
        name="check_return_eligibility",
        parameters={
            "order_id": {"type": "str", "required": False, "description": "Order ID to check. Omit to check most recent order."},
            "reason": {"type": "str", "required": False, "description": "Return reason if known — defective claims return requires_escalation instead of eligible."},
        },
        handler=check_return_eligibility,
    ),
    "initiate_return": ToolDefinition(
        name="initiate_return",
        parameters={
            "order_id": {"type": "str", "required": False, "description": "Order ID to return. Omit to use most recent order."},
            "reason": {"type": "str", "required": True, "description": f"Return reason: {_REASON_LIST}."},
        },
        handler=initiate_return,
    ),
    "check_missing_package": ToolDefinition(
        name="check_missing_package",
        parameters={
            "order_id": {"type": "str", "required": False, "description": "Order ID to check. Omit to use most recently delivered order."},
        },
        handler=check_missing_package,
    ),
    "get_refund_status": ToolDefinition(
        name="get_refund_status",
        parameters={
            "order_id": {"type": "str", "required": False, "description": "Order ID to check refund for. Omit to return all refunds for this customer."},
        },
        handler=get_refund_status,
    ),
}
