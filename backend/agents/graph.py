from langgraph.graph import StateGraph, START, END

from backend.agents.state import AgentState
from backend.agents.conversation import conversation_agent_node
from backend.agents.knowledge_service import knowledge_service_node


def _route_after_conversation(state: AgentState) -> str:
    pending = state.get("pending_service", "")
    if pending == "knowledge":
        return "knowledge_service"
    # "escalation" and "action" nodes added in Phase 3 steps 3-4
    return END


def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("conversation_agent", conversation_agent_node)
    builder.add_node("knowledge_service", knowledge_service_node)

    builder.add_edge(START, "conversation_agent")
    builder.add_conditional_edges(
        "conversation_agent",
        _route_after_conversation,
        {
            "knowledge_service": "knowledge_service",
            END: END,
        },
    )
    # After knowledge_service returns chunks, go back to conversation_agent
    # so it can generate the customer-facing response.
    builder.add_edge("knowledge_service", "conversation_agent")

    return builder.compile()


# Module-level compiled graph — constructed once, reused across all requests
graph = build_graph()
