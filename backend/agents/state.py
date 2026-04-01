from typing import TypedDict


class AgentState(TypedDict):
    messages: list[dict]        # [{"role": "customer"|"agent", "content": str}]
    customer_id: str
    customer_context: dict      # purchase history, risk score (populated in Phase 3 step 5)
    retrieved_context: list     # KB chunks returned by knowledge_service (cleared each turn)
    action_results: list        # Results from action_service calls this turn
    confidence: float           # conversation agent's confidence in its response
    requires_escalation: bool
    escalation_reason: str      # why escalation was triggered
    actions_taken: list[dict]   # audit trail of all service calls this turn
    response: str               # final customer-facing response text
    pending_service: str        # internal routing: "knowledge"|"action"|"escalation"|"" (empty = none pending)
    pending_action: dict        # {"tool": "cancel_order", "params": {...}} set by conversation agent
    inferred_intent: str               # Raw output of _classify_intent: "knowledge_query"|"action_request"|"escalation_request"|"needs_clarification"|"general"
    last_clarification_source: str    # Why the last clarifying question was asked: "intent"|"emotion"|"" (empty = none asked)
    context_summary: str               # Plain-text summary of customer messages at time of escalation
    consecutive_blocks: int            # Number of consecutive input-guard blocks this conversation; resets to 0 on any unblocked turn
    service_call_count: int            # Number of service calls initiated this turn; reset to 0 at turn start, checked against service_call_limit
