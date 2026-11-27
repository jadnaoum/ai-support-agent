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
    actions_taken: list[dict]   # audit trail of service calls THIS turn (empty at turn start; used by service_ran gate)
    prior_turn_actions: list    # actions_taken persisted from prior turns; used only by the confirmation gate
    earlier_context: list       # tool results + top KB passages from the last few turns (reply step + output guard only)
    response: str               # final customer-facing response text
    pending_service: str        # internal routing: "knowledge"|"action"|"" (empty = none pending)
    pending_action: dict        # {"tool": "cancel_order", "params": {...}} set by conversation agent
    inferred_intent: str               # Raw output of _classify_intent: "knowledge_query"|"action_request"|"escalation_request"|"needs_clarification"|"general"|"small_talk"
    last_clarification_source: str    # Why the last clarifying question was asked: "intent"|"emotion"|"" (empty = none asked)
    context_summary: str               # Plain-text summary of customer messages at time of escalation
    consecutive_blocks: int            # Number of consecutive input-guard blocks this conversation; resets to 0 on any unblocked turn
    consecutive_small_talk: int        # small_talk turns in a row; resets to 0 on any other turn that passes the input guard
    total_small_talk: int              # small_talk turns this conversation (never resets)
    provocative_count: int             # input-guard "provocative" blocks this conversation (never resets)
    consecutive_high_negative: int     # frustrated turns in a row (input guard emotion); resets to 0 on any calm turn that passes the guard
    service_call_count: int            # Number of service calls initiated this turn; reset to 0 at turn start, checked against service_call_limit
    classifier_reasoning: str         # Debug only: LLM's explanation of its intent classification (empty unless debug_classifier is set in mock_context)
