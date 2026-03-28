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
    process_refund,
)
from prompts.loader import get_prompt


@dataclass
class ToolDefinition:
    name: str
    description: str          # shown to LLM for intent extraction
    parameters: dict          # {param_name: {"type": str, "required": bool, "description": str}}
    handler: Callable         # async (db, customer_id, **params) -> dict


# DESCRIPTIONS — edit in prompts/production.yaml
TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "track_order": ToolDefinition(
        name="track_order",
        description=get_prompt("tool_track_order_description"),
        parameters={
            "order_id": {"type": "str", "required": False, "description": "Order ID to look up. Omit to use most recent order."},
        },
        handler=track_order,
    ),
    "cancel_order": ToolDefinition(
        name="cancel_order",
        description=get_prompt("tool_cancel_order_description"),
        parameters={
            "order_id": {"type": "str", "required": False, "description": "Order ID to cancel. Omit to cancel most recent order."},
            "reason": {"type": "str", "required": False, "description": "Cancellation reason."},
        },
        handler=cancel_order,
    ),
    "process_refund": ToolDefinition(
        name="process_refund",
        description=get_prompt("tool_process_refund_description"),
        parameters={
            "order_id": {"type": "str", "required": False, "description": "Order ID to refund. Omit to refund most recent order."},
            "amount": {"type": "float", "required": False, "description": "Partial refund amount. Omit for full refund."},
            "reason": {"type": "str", "required": False, "description": "Refund reason: defective, changed_mind, wrong_item, late_delivery, other."},
        },
        handler=process_refund,
    ),
}
