from langgraph.graph import StateGraph, START, END

from backend.agents.state import AgentState
from backend.agents.supervisor import supervisor_node, route_after_supervisor
from backend.agents.knowledge_agent import knowledge_agent_node


def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("supervisor", supervisor_node)
    builder.add_node("knowledge_agent", knowledge_agent_node)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"knowledge_agent": "knowledge_agent"},
    )
    builder.add_edge("knowledge_agent", END)

    return builder.compile()


# Module-level compiled graph — constructed once, reused across all requests
graph = build_graph()
