from typing import TypedDict


class AgentState(TypedDict):
    messages: list[dict]        # [{"role": "customer"|"agent", "content": str}]
    customer_id: str
    customer_context: dict      # Phase 2: always {}; Phase 3 will populate from DB
    current_intent: str         # set by supervisor
    routing_decision: str       # set by supervisor (for audit logging)
    confidence: float           # set by knowledge agent
    response: str               # final response text
    requires_escalation: bool   # Phase 3: set when confidence < threshold
    actions_taken: list[dict]   # audit trail
