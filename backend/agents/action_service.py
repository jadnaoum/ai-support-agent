"""
Action service — non-customer-facing.

Receives a structured action request from the conversation agent (tool name + params),
executes it via the tool registry, and returns results to the conversation agent.
Does NOT generate any customer-facing text.
"""
import re

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import AgentState
from backend.tools.registry import TOOL_REGISTRY

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


async def action_service_node(state: AgentState, config: dict) -> dict:
    """LangGraph node: validate pending_action → execute tool → return results."""
    db: AsyncSession = config["configurable"]["db"]

    pending = state.get("pending_action") or {}
    tool_name = pending.get("tool", "")
    params = dict(pending.get("params") or {})

    # Strip null params — tools use defaults for missing values
    params = {k: v for k, v in params.items() if v is not None}

    if not tool_name or tool_name not in TOOL_REGISTRY:
        result = {"success": False, "error": f"Unknown action: '{tool_name}'."}
    else:
        # Combine prior-turn and current-turn actions for the confirmation gate.
        # prior_turn_actions holds confirmation_required entries from previous turns.
        # actions_taken holds entries added within the current graph traversal.
        combined_actions = (state.get("prior_turn_actions") or []) + (state.get("actions_taken") or [])

        mock = (config.get("configurable") or {}).get("mock_account_state")
        if mock:
            from backend.agents.mock_tools import mock_tool_call  # noqa: PLC0415
            result = mock_tool_call(
                tool_name, params, mock,
                state.get("customer_id", ""),
                combined_actions,
            )
        else:
            customer_id = state.get("customer_id", "")
            if not customer_id or not _UUID_RE.match(customer_id):
                result = {"success": False, "error": "No valid customer account context is available."}
            else:
                tool = TOOL_REGISTRY[tool_name]
                # Always inject db and customer_id; tools ignore extra kwargs via their signatures
                try:
                    result = await tool.handler(
                        db=db,
                        customer_id=customer_id,
                        actions_taken=combined_actions,
                        **params,
                    )
                except Exception as e:
                    result = {"success": False, "error": f"Action failed: {str(e)}", "unhandled_error": True}

    # Resolve the order_id that was actually used (may differ from params when LLM omitted it).
    # Note: some tools return "details" as a plain string (e.g. reason_required gate) — guard against that.
    _details = result.get("details")
    resolved_order_id = result.get("order_id") or (
        _details.get("order_id") if isinstance(_details, dict) else None
    )

    # Machine-readable result for the escalation context summary.
    # Priority: confirmation_pending > success > structured reason code > failed.
    result_detail = (
        "confirmation_pending" if result.get("confirmation_required")
        else "success" if result.get("success")
        else result.get("reason") or "failed"
    )

    return {
        "action_results": (state.get("action_results") or []) + [result],
        "pending_service": "",
        "pending_action": {},
        "actions_taken": (state.get("actions_taken") or []) + [
            {
                "service": "action_service",
                "action": tool_name or "unknown",
                "params": pending.get("params", {}),
                "success": result.get("success", False),
                "order_id": resolved_order_id,
                "confirmation_required": result.get("confirmation_required", False),
                "result_detail": result_detail,
            }
        ],
    }
