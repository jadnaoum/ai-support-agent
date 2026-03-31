"""
Conversation agent — the only customer-facing node.

Two-pass design:
  Pass 1 (no service results yet): classify intent → set pending_service.
          If intent is "general", respond directly without calling a service.
  Pass 2 (service results available): optionally loop for another service call
          (if the result signals more may be needed and the call limit allows),
          then generate the customer-facing response.
"""
import json
import litellm

from backend.config import get_settings
from backend.agents.state import AgentState
from backend.agents.escalation import handle_escalation, build_context_summary
from backend.guardrails.input_guard import check_input, log_blocked_attempt
from backend.guardrails.output_guard import check_output, log_output_guard_blocked
from prompts.loader import get_prompt

settings = get_settings()

# PROMPTS — edit in prompts/production.yaml
INTENT_PROMPT = get_prompt("intent_prompt")
RESPONSE_PROMPT = get_prompt("response_prompt")
REDIRECT_PROMPT = get_prompt("redirect_prompt")
LOOP_DECISION_PROMPT = get_prompt("loop_decision_prompt")


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
            f"\nCustomer: {customer_context['name']}{order_summary}"
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


async def _generate_redirect(block_count: int, category: str) -> str:
    """Generate a natural redirect message when the input guard blocks a message.

    Uses block_count (1 or 2) and category (off_topic/abusive/prompt_injection)
    to apply the correct tone per the guidelines in REDIRECT_PROMPT.
    Falls back to a generic safe message on any LLM error.
    """
    system = REDIRECT_PROMPT.format(block_count=block_count, category=category)
    try:
        result = await litellm.acompletion(
            model=settings.litellm_model,
            messages=[{"role": "system", "content": system}],
            stream=False,
        )
        return result.choices[0].message.content.strip()
    except Exception:
        return "I'm here to help with your orders and account questions. Is there something I can assist you with?"


async def _generate_emotion_clarification(state: AgentState, clarification_hint: str) -> str:
    """Generate an empathetic clarifying question when the customer is frustrated.

    Uses the standard response prompt so warmth instructions apply, with the
    clarification hint injected into the context section.
    """
    context_section = (
        f"\nNote: The customer appears frustrated or distressed. "
        f"Briefly acknowledge their frustration, then ask: {clarification_hint}"
    )
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


def _service_needs_loop(state: AgentState) -> bool:
    """
    Returns True if the most recent service result signals there may be more to do.
    Only action_service results carry structured signals (success, reason, available_action).
    Knowledge service results have no such signals and are always treated as clean.
    """
    last_service = next(
        (e["service"] for e in reversed(state.get("actions_taken") or [])
         if e.get("service") in ("knowledge_service", "action_service")),
        None,
    )
    if last_service != "action_service":
        return False

    action_results = state.get("action_results") or []
    if not action_results:
        return False

    last = action_results[-1]
    return (
        not last.get("success", True)
        or bool(last.get("available_action"))
        or bool(last.get("reason"))
    )


async def _classify_next_step(state: AgentState) -> dict:
    """
    After at least one service has run, decide whether another call is needed.
    Returns {"next": "respond"|"knowledge"|"action", "action": ..., "params": ...}
    Falls back to {"next": "respond"} on any parse error.
    """
    context_section = _build_context_section(state)
    last_message = next(
        (m["content"] for m in reversed(state["messages"]) if m["role"] == "customer"),
        "",
    )
    try:
        result = await litellm.acompletion(
            model=settings.litellm_model,
            messages=[
                {"role": "system", "content": LOOP_DECISION_PROMPT.format(context_section=context_section)},
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
        return json.loads(raw.strip())
    except Exception:
        return {"next": "respond"}


async def _do_escalate(reason: str, state: AgentState, config: dict) -> dict:
    """
    Call the escalation handler inline and return the full state update.

    Callers may spread extra fields (e.g. confidence, consecutive_blocks) over
    the returned dict to supply turn-specific values.
    """
    _db = config.get("configurable", {}).get("db")
    _conv_id = config.get("configurable", {}).get("conversation_id", "")
    summary = build_context_summary(
        messages=state.get("messages") or [],
        actions_taken=state.get("actions_taken") or [],
        retrieved_context=state.get("retrieved_context") or [],
        reason=reason,
    )
    context = {
        "db": _db,
        "conversation_id": _conv_id,
        "confidence": state.get("confidence", 0.0),
        "messages": state.get("messages") or [],
        "context_summary": summary,
    }
    handoff = await handle_escalation(reason, context)
    return {
        "response": handoff,
        "requires_escalation": True,
        "pending_service": "",
        "escalation_reason": reason,
        "last_clarification_source": "",
        "context_summary": summary,
        "actions_taken": (state.get("actions_taken") or []) + [
            {"service": "escalation_handler", "action": "escalate", "reason": reason}
        ],
    }


async def conversation_agent_node(state: AgentState, config: dict) -> dict:
    """
    Central customer-facing LangGraph node.

    Pass 1 — no service results yet: classify intent and route to a service,
              or respond directly if no service is needed.
    Pass 2 — service results available: optionally loop for another service call,
              then generate the customer-facing response.
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
            # Persist audit log for blocked attempts (skip in test mode where conversation_id is "")
            _db = config.get("configurable", {}).get("db")
            _conv_id = config.get("configurable", {}).get("conversation_id", "")
            if _db and _conv_id:
                await log_blocked_attempt(_db, _conv_id, last_message, guard)
                await _db.commit()

            # Abusive → immediate escalation, bypasses the block counter entirely
            if guard.get("reason") == "abusive":
                return {
                    **await _do_escalate("abusive_input", state, config),
                    "confidence": 1.0,
                    "consecutive_blocks": state.get("consecutive_blocks", 0),
                    "service_call_count": 0,
                }

            new_blocks = state.get("consecutive_blocks", 0) + 1

            # 3rd consecutive block → escalate to human
            if new_blocks >= 3:
                return {
                    **await _do_escalate("repeated_blocks", state, config),
                    "confidence": 1.0,
                    "consecutive_blocks": new_blocks,
                    "service_call_count": 0,
                }

            # 1st or 2nd block → LLM-generated redirect with category- and count-specific tone
            redirect = await _generate_redirect(new_blocks, guard.get("reason", "prompt_injection"))
            return {
                "response": redirect,
                "pending_service": "",
                "confidence": 1.0,
                "consecutive_blocks": new_blocks,
                "last_clarification_source": "",
                "service_call_count": 0,
            }

        # Message passed the guard — reset the consecutive block counter
        emotion = guard.get("emotion", "")
        intent, confidence, action_details = await _classify_intent(state)

        # --- Emotion path ---
        # High negative emotion + unclear intent + no clarifying question asked yet this
        # conversation → ask one empathetic clarifying question.
        # If intent is already actionable, the emotion flag only adjusts tone via the
        # response prompt — no extra step needed.
        if (
            emotion == "high_negative"
            and intent == "needs_clarification"
            and not state.get("last_clarification_source", "")
        ):
            clarification_hint = action_details.get(
                "clarification_prompt",
                "Could you tell me more about what happened?",
            )
            question = await _generate_emotion_clarification(state, clarification_hint)
            out_guard = await check_output(question, state)
            if out_guard["safe"]:
                return {
                    "response": question,
                    "confidence": confidence,
                    "pending_service": "",
                    "last_clarification_source": "emotion",
                    "consecutive_blocks": 0,
                    "service_call_count": 0,
                }
            _db = config.get("configurable", {}).get("db")
            _conv_id = config.get("configurable", {}).get("conversation_id", "")
            if _db and _conv_id:
                await log_output_guard_blocked(_db, _conv_id, question, out_guard["reason"])
                await _db.commit()
            return {
                **await _do_escalate("unable_to_clarify", state, config),
                "confidence": confidence,
                "consecutive_blocks": 0,
                "service_call_count": 0,
            }

        if intent == "knowledge_query":
            return {
                "pending_service": "knowledge",
                "confidence": confidence,
                "last_clarification_source": "",
                "consecutive_blocks": 0,
                "service_call_count": 1,
            }

        if intent == "action_request":
            return {
                "pending_service": "action",
                "pending_action": action_details,
                "confidence": confidence,
                "last_clarification_source": "",
                "consecutive_blocks": 0,
                "service_call_count": 1,
            }

        if intent == "escalation_request":
            return {
                **await _do_escalate("customer_requested", state, config),
                "confidence": confidence,
                "consecutive_blocks": 0,
                "service_call_count": 0,
            }

        if intent == "needs_clarification":
            # Cap: if we already asked a clarifying question this conversation (any source),
            # escalate instead of asking again.
            if state.get("last_clarification_source", ""):
                return {
                    **await _do_escalate("unable_to_clarify", state, config),
                    "confidence": confidence,
                    "consecutive_blocks": 0,
                    "service_call_count": 0,
                }
            clarification_question = action_details.get(
                "clarification_prompt",
                "Could you provide a bit more detail so I can help you?",
            )
            out_guard = await check_output(clarification_question, state)
            if out_guard["safe"]:
                return {
                    "response": clarification_question,
                    "confidence": confidence,
                    "pending_service": "",
                    "last_clarification_source": "intent",
                    "consecutive_blocks": 0,
                    "service_call_count": 0,
                }
            # Output guard blocked the clarifying question — log and escalate
            _db = config.get("configurable", {}).get("db")
            _conv_id = config.get("configurable", {}).get("conversation_id", "")
            if _db and _conv_id:
                await log_output_guard_blocked(_db, _conv_id, clarification_question, out_guard["reason"])
                await _db.commit()
            return {
                **await _do_escalate("unable_to_clarify", state, config),
                "confidence": confidence,
                "consecutive_blocks": 0,
                "service_call_count": 0,
            }

        # general — answer directly without a service call
        response = await _generate_response(state)
        out_guard = await check_output(response, state)
        if not out_guard["safe"]:
            _db = config.get("configurable", {}).get("db")
            _conv_id = config.get("configurable", {}).get("conversation_id", "")
            if _db and _conv_id:
                await log_output_guard_blocked(_db, _conv_id, response, out_guard["reason"])
                await _db.commit()
            return {
                **await _do_escalate("policy_exception", state, config),
                "confidence": confidence,
                "consecutive_blocks": 0,
                "service_call_count": 0,
            }
        return {
            "response": response,
            "confidence": confidence,
            "pending_service": "",
            "last_clarification_source": "",
            "consecutive_blocks": 0,
            "service_call_count": 0,
        }

    # Pass 2: service results are available.
    # Check escalation conditions first, then decide whether to loop or respond.
    retrieved = state.get("retrieved_context") or []
    if retrieved:
        top_similarity = retrieved[0]["similarity"]
        if top_similarity < settings.confidence_threshold:
            return {
                **await _do_escalate("low_confidence", state, config),
                "confidence": top_similarity,
            }

    # Escalate if any process_refund result came back as pending_review.
    # A human agent must follow up — the conversation agent should not handle this silently.
    for action_result in state.get("action_results") or []:
        if action_result.get("status") == "pending_review":
            return {
                **await _do_escalate("policy_exception", state, config),
                "confidence": retrieved[0]["similarity"] if retrieved else state.get("confidence", 1.0),
            }

    # Loop decision: if the last service result signals more may be needed and we're under
    # the call limit, ask the LLM whether to make another call before responding.
    # Clean successes (no error, no rejection reason, no available_action) skip this entirely.
    service_call_count = state.get("service_call_count", 0)
    if service_call_count < settings.service_call_limit and _service_needs_loop(state):
        next_step = await _classify_next_step(state)
        if next_step.get("next") == "knowledge":
            return {
                "pending_service": "knowledge",
                "service_call_count": service_call_count + 1,
            }
        if next_step.get("next") == "action":
            action_details = {
                "tool": next_step.get("action", ""),
                "params": next_step.get("params") or {},
            }
            return {
                "pending_service": "action",
                "pending_action": action_details,
                "service_call_count": service_call_count + 1,
            }
        # "respond" falls through to response generation

    # Generate the customer-facing response using service results
    response = await _generate_response(state)
    top_similarity = retrieved[0]["similarity"] if retrieved else state.get("confidence", 1.0)

    out_guard = await check_output(response, state)
    if not out_guard["safe"]:
        _db = config.get("configurable", {}).get("db")
        _conv_id = config.get("configurable", {}).get("conversation_id", "")
        if _db and _conv_id:
            await log_output_guard_blocked(_db, _conv_id, response, out_guard["reason"])
            await _db.commit()
        return {
            **await _do_escalate("policy_exception", state, config),
            "confidence": top_similarity,
        }

    return {
        "response": response,
        "confidence": top_similarity,
        "pending_service": "",
        "last_clarification_source": "",
    }
