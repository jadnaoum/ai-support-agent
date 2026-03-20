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

settings = get_settings()

# PROMPTS — edit here to tune agent behavior

INTENT_PROMPT = """You are an intent classifier for a customer support system.
Classify the customer's latest message into exactly one category:
- knowledge_query: asking about policies, shipping, returns, products, warranties, or account info
- action_request: wants to track an order, cancel an order, request a refund, or view order history
- escalation_request: explicitly asking to speak to a human agent or supervisor
- general: greeting, thank you, or a simple message that needs no information lookup

For action_request, also extract the action and any parameters mentioned.
Available actions: track_order, cancel_order, process_refund, get_order_history
Use null for parameters the customer did not mention.

Respond with valid JSON only, no markdown.
Examples:
{"intent": "knowledge_query", "confidence": 0.9}
{"intent": "action_request", "confidence": 0.95, "action": "cancel_order", "params": {"order_id": "12345", "reason": "changed_mind"}}
{"intent": "action_request", "confidence": 0.9, "action": "track_order", "params": {"order_id": null}}
{"intent": "escalation_request", "confidence": 0.95}
{"intent": "general", "confidence": 0.99}"""

RESPONSE_PROMPT = """You are a helpful, empathetic customer support agent for an e-commerce store.
You handle questions about orders, returns, shipping, payments, and product policies.
Be concise, warm, and specific. Never make up order numbers, dates, or prices.
If you are not sure about something, say so honestly and offer to connect the customer with a specialist.
{context_section}"""


def _build_context_section(state: AgentState) -> str:
    chunks = state.get("retrieved_context") or []
    action_results = state.get("action_results") or []
    parts = []
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
        intent, confidence, action_details = await _classify_intent(state)

        if intent == "knowledge_query":
            return {"pending_service": "knowledge", "confidence": confidence}

        if intent == "action_request":
            return {
                "pending_service": "action",
                "pending_action": action_details,
                "confidence": confidence,
            }

        if intent == "escalation_request":
            return {
                "pending_service": "escalation",  # escalation_handler added in Phase 3 step 4
                "requires_escalation": True,
                "escalation_reason": "customer_requested",
                "confidence": confidence,
            }

        # general — answer directly without a service call
        response = await _generate_response(state)
        return {"response": response, "confidence": confidence, "pending_service": ""}

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
            }

    # Generate the customer-facing response using service results
    response = await _generate_response(state)
    top_similarity = retrieved[0]["similarity"] if retrieved else state.get("confidence", 1.0)
    return {"response": response, "confidence": top_similarity, "pending_service": ""}
