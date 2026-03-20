from langgraph.graph import StateGraph, START, END

from backend.agents.state import AgentState
from backend.agents.conversation import conversation_agent_node
from backend.agents.knowledge_service import knowledge_service_node
from backend.agents.action_service import action_service_node
from backend.agents.escalation import escalation_handler_node


def _route_after_conversation(state: AgentState) -> str:
    pending = state.get("pending_service", "")
    if pending == "knowledge":
        return "knowledge_service"
    if pending == "action":
        return "action_service"
    if pending == "escalation":
        return "escalation_handler"
    return END


def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("conversation_agent", conversation_agent_node)
    builder.add_node("knowledge_service", knowledge_service_node)
    builder.add_node("action_service", action_service_node)
    builder.add_node("escalation_handler", escalation_handler_node)

    builder.add_edge(START, "conversation_agent")
    builder.add_conditional_edges(
        "conversation_agent",
        _route_after_conversation,
        {
            "knowledge_service": "knowledge_service",
            "action_service": "action_service",
            "escalation_handler": "escalation_handler",
            END: END,
        },
    )
    # Knowledge and action services return results to conversation_agent
    builder.add_edge("knowledge_service", "conversation_agent")
    builder.add_edge("action_service", "conversation_agent")
    # Escalation handler ends the turn directly
    builder.add_edge("escalation_handler", END)

    return builder.compile()


# Module-level compiled graph — constructed once, reused across all requests
graph = build_graph()
