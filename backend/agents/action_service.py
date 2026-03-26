"""
Action service — non-customer-facing.

Receives a structured action request from the conversation agent (tool name + params),
executes it via the tool registry, and returns results to the conversation agent.
Does NOT generate any customer-facing text.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.state import AgentState
from backend.tools.registry import TOOL_REGISTRY


async def action_service_node(state: AgentState, config: dict) -> dict:
    """LangGraph node: validate pending_action → execute tool → return results."""
    db: AsyncSession = config["configurable"]["db"]

    pending = state.get("pending_action") or {}
    tool_name = pending.get("tool", "")
    params = dict(pending.get("params") or {})

    # Strip null params — tools use defaults for missing values
    params = {k: v for k, v in params.items() if v is not None}

    # Inject risk_score for process_refund from pre-loaded customer context.
    # This must come AFTER null-stripping so the LLM cannot supply or override it.
    if tool_name == "process_refund":
        params["risk_score"] = float(
            (state.get("customer_context") or {}).get("risk_score", 0.0) or 0.0
        )

    if not tool_name or tool_name not in TOOL_REGISTRY:
        result = {"success": False, "error": f"Unknown action: '{tool_name}'."}
    else:
        tool = TOOL_REGISTRY[tool_name]
        # Always inject db and customer_id; tools ignore extra kwargs via their signatures
        try:
            result = await tool.handler(
                db=db,
                customer_id=state.get("customer_id", ""),
                **params,
            )
        except Exception as e:
            result = {"success": False, "error": f"Action failed: {str(e)}"}

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
            }
        ],
    }
