from backend.agents.state import AgentState


async def supervisor_node(state: AgentState, config: dict) -> dict:
    # Phase 2: hardcoded routing — always send to knowledge agent.
    # Phase 3 will use LLM intent classification to route between
    # knowledge_agent, action_agent, and escalation_handler.
    return {
        "current_intent": "knowledge_query",
        "routing_decision": "hardcoded_knowledge_phase2",
    }


def route_after_supervisor(state: AgentState) -> str:
    # Phase 3 will branch here based on state["current_intent"]
    return "knowledge_agent"
