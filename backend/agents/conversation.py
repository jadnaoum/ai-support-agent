"""
Conversation agent — the only customer-facing node.

Two-pass design:
  Pass 1 (no service results yet): classify intent → set pending_service.
          If intent is "general", respond directly without calling a service.
  Pass 2 (service results available): generate the customer-facing response
          using retrieved_context / action_results.
"""
import json
import litellm

from backend.config import get_settings
from backend.agents.state import AgentState
from backend.guardrails.input_guard import check_input
from backend.guardrails.output_guard import check_output
from prompts.loader import get_prompt

settings = get_settings()

# PROMPTS — edit in prompts/production.yaml
INTENT_PROMPT = get_prompt("intent_prompt")
RESPONSE_PROMPT = get_prompt("response_prompt")


def _build_context_section(state: AgentState) -> str:
    chunks = state.get("retrieved_context") or []
    action_results = state.get("action_results") or []
    customer_context = state.get("customer_context") or {}
    parts = []

    if customer_context.get("name"):
        orders = customer_context.get("recent_orders") or []
        order_summary = ""
        if orders:
            lines = [
                f"  - Order {o['order_id'][:8]}… | {o['status']} | ${o['total']:.2f}"
                for o in orders[:3]
            ]
            order_summary = "\nRecent orders:\n" + "\n".join(lines)
        parts.append(
            f"\nCustomer: {customer_context['name']} (risk score: {customer_context.get('risk_score', 'n/a')}){order_summary}"
        )

    if chunks:
        kb_text = "\n\n---\n\n".join(
            f"Source: {c['title']} ({c['category']})\n{c['chunk_text']}"
            for c in chunks
        )
        parts.append(f"\nKnowledge Base:\n{kb_text}")
    if action_results:
        parts.append(f"\nAction Results:\n{json.dumps(action_results, indent=2)}")
    return "\n".join(parts)


async def _classify_intent(state: AgentState) -> tuple[str, float, dict]:
    """
    Call LLM to classify the customer's latest message.
    Returns (intent, confidence, action_details).
    action_details is {"tool": ..., "params": {...}} for action_request, {} otherwise.
    """
    last_message = ""
    for msg in reversed(state["messages"]):
        if msg["role"] == "customer":
            last_message = msg["content"]
            break

    try:
        result = await litellm.acompletion(
            model=settings.litellm_model,
            messages=[
                {"role": "system", "content": INTENT_PROMPT},
                {"role": "user", "content": last_message},
            ],
            stream=False,
        )
        raw = result.choices[0].message.content.strip()
        # Strip markdown code fences if the LLM wraps the JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        intent = parsed.get("intent", "general")
        confidence = float(parsed.get("confidence", 0.8))
        action_details = {}
        if intent == "action_request":
            action_details = {
                "tool": parsed.get("action", ""),
                "params": parsed.get("params") or {},
            }
        if intent == "needs_clarification":
            action_details = {
                "clarification_prompt": parsed.get("clarification_prompt", "Could you provide a bit more detail so I can help you?"),
            }
        return intent, confidence, action_details
    except Exception:
        return "general", 0.5, {}


async def _generate_response(state: AgentState) -> str:
    """Generate the customer-facing response from conversation history + service results."""
    context_section = _build_context_section(state)
    system_content = RESPONSE_PROMPT.format(context_section=context_section)

    messages_for_llm = [{"role": "system", "content": system_content}]
    role_map = {"customer": "user", "agent": "assistant"}
    for msg in state["messages"][-settings.max_context_messages:]:
        if msg["role"] in role_map:
            messages_for_llm.append({"role": role_map[msg["role"]], "content": msg["content"]})

    result = await litellm.acompletion(
        model=settings.litellm_model,
        messages=messages_for_llm,
        stream=False,
    )
    return result.choices[0].message.content


async def conversation_agent_node(state: AgentState, config: dict) -> dict:
    """
    Central customer-facing LangGraph node.

    Pass 1 — no service results yet: classify intent and route to a service,
              or respond directly if no service is needed.
    Pass 2 — service results available: generate the customer-facing response.
    """
    # Pass 2 if any service has already run (actions_taken is populated by services).
    # Checking actions_taken is more reliable than checking retrieved_context/action_results
    # because those can be empty lists even after a service ran (e.g. no KB chunks found).
    service_ran = bool(state.get("actions_taken"))

    # Pass 1: intent classification
    if not service_ran:
        # --- Input guard ---
        last_message = next(
            (m["content"] for m in reversed(state["messages"]) if m["role"] == "customer"),
            "",
        )
        guard = await check_input(last_message)
        if not guard["safe"]:
            return {
                "response": guard["blocked_response"],
                "pending_service": "",
                "confidence": 1.0,
            }

        intent, confidence, action_details = await _classify_intent(state)

        if intent == "knowledge_query":
            return {"pending_service": "knowledge", "confidence": confidence, "last_turn_was_clarification": False}

        if intent == "action_request":
            return {
                "pending_service": "action",
                "pending_action": action_details,
                "confidence": confidence,
                "last_turn_was_clarification": False,
            }

        if intent == "escalation_request":
            return {
                "pending_service": "escalation",  # escalation_handler added in Phase 3 step 4
                "requires_escalation": True,
                "escalation_reason": "customer_requested",
                "confidence": confidence,
                "last_turn_was_clarification": False,
            }

        if intent == "needs_clarification":
            # Cap: if we already asked a clarifying question last turn, escalate instead
            # of asking again — the customer's response is still ambiguous after one attempt.
            if state.get("last_turn_was_clarification", False):
                return {
                    "pending_service": "escalation",
                    "requires_escalation": True,
                    "escalation_reason": "unable_to_clarify",
                    "confidence": confidence,
                    "last_turn_was_clarification": False,
                }
            clarification_question = action_details.get(
                "clarification_prompt",
                "Could you provide a bit more detail so I can help you?",
            )
            out_guard = check_output(clarification_question, state)
            if out_guard["safe"]:
                return {
                    "response": clarification_question,
                    "confidence": confidence,
                    "pending_service": "",
                    "last_turn_was_clarification": True,
                }
            # Output guard blocked the clarifying question — escalate
            return {
                "pending_service": "escalation",
                "requires_escalation": True,
                "escalation_reason": "unable_to_clarify",
                "confidence": confidence,
                "last_turn_was_clarification": False,
            }

        # general — answer directly without a service call
        response = await _generate_response(state)
        out_guard = check_output(response, state)
        if not out_guard["safe"]:
            return {
                "pending_service": "escalation",
                "requires_escalation": True,
                "escalation_reason": "policy_exception",
                "confidence": confidence,
                "last_turn_was_clarification": False,
            }
        return {"response": response, "confidence": confidence, "pending_service": "", "last_turn_was_clarification": False}

    # Pass 2: check confidence before generating response.
    # If KB results are present but below threshold, escalate instead of guessing.
    retrieved = state.get("retrieved_context") or []
    if retrieved:
        top_similarity = retrieved[0]["similarity"]
        if top_similarity < settings.confidence_threshold:
            return {
                "pending_service": "escalation",
                "requires_escalation": True,
                "escalation_reason": "low_confidence",
                "confidence": top_similarity,
                "last_turn_was_clarification": False,
            }

    # Escalate if any process_refund result came back as pending_review.
    # A human agent must follow up — the conversation agent should not handle this silently.
    for action_result in state.get("action_results") or []:
        if action_result.get("status") == "pending_review":
            return {
                "pending_service": "escalation",
                "requires_escalation": True,
                "escalation_reason": "policy_exception",
                "confidence": retrieved[0]["similarity"] if retrieved else state.get("confidence", 1.0),
                "last_turn_was_clarification": False,
            }

    # Generate the customer-facing response using service results
    response = await _generate_response(state)
    top_similarity = retrieved[0]["similarity"] if retrieved else state.get("confidence", 1.0)

    out_guard = check_output(response, state)
    if not out_guard["safe"]:
        return {
            "pending_service": "escalation",
            "requires_escalation": True,
            "escalation_reason": "policy_exception",
            "confidence": top_similarity,
            "last_turn_was_clarification": False,
        }

    return {"response": response, "confidence": top_similarity, "pending_service": "", "last_turn_was_clarification": False}
