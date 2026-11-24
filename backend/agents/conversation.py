"""
Conversation agent — the only customer-facing node.

Two-pass design:
  Pass 1 (no service results yet): classify intent → set pending_service.
          If intent is "general", respond directly without calling a service.
  Pass 2 (service results available): optionally loop for another service call
          (if the result signals more may be needed and the call limit allows),
          then generate the customer-facing response.
"""
import asyncio
import json
import logging
import litellm

logger = logging.getLogger(__name__)

LLM_TIMEOUT = 30  # seconds per LLM call — prevents hung API requests from stalling the graph

from backend.config import get_settings
from backend.agents.state import AgentState
from backend.agents.order_context import format_recent_orders
from backend.agents.escalation import handle_escalation, build_context_summary, run_escalation_side_effects
from backend.guardrails.input_guard import check_input, log_blocked_attempt
from backend.guardrails.output_guard import check_output, log_output_guard_blocked
from backend.llm_json import extract_json_object
from prompts.loader import get_prompt, get_response_guidance

settings = get_settings()

# PROMPTS — edit in prompts/production.yaml
INTENT_PROMPT = get_prompt("intent_prompt")
RESPONSE_PROMPT = get_prompt("response_prompt")
REDIRECT_PROMPT = get_prompt("redirect_prompt")
LOOP_DECISION_PROMPT = get_prompt("loop_decision_prompt")
ESCALATION_SUMMARY_PROMPT = get_prompt("escalation_summary_prompt")
HANDOFF_MESSAGE_PROMPT = get_prompt("handoff_message_prompt")

# Small-talk and provocative limits (decided 2026-11-24). The warning turn is
# still answered; the escalation turn skips response generation entirely.
SMALL_TALK_WARN_CONSECUTIVE = 2
SMALL_TALK_ESCALATE_CONSECUTIVE = 3
SMALL_TALK_WARN_TOTAL = 4
SMALL_TALK_ESCALATE_TOTAL = 5
PROVOCATIVE_ESCALATE = 2  # the 1st provocative message gets a warning redirect


def _format_conversation(messages: list, last: int = 12) -> str:
    """Plain-text transcript of the last turns, for the handoff prompts."""
    role_label = {"customer": "Customer", "agent": "Agent"}
    return "\n".join(
        f"{role_label.get(m.get('role'), m.get('role', '?'))}: {m.get('content', '')}"
        for m in (messages or [])[-last:]
        if m.get("role") in role_label
    ) or "(no messages)"


async def _generate_handoff_message(messages: list, reason: str) -> str:
    """Customer-facing handoff sentence written for this conversation and
    reason (replaces the fixed _HANDOFF_MESSAGE on code-triggered escalations).
    Returns "" on any error — the caller then keeps the fixed message."""
    try:
        result = await litellm.acompletion(
            model=settings.litellm_model,
            messages=[{
                "role": "user",
                "content": HANDOFF_MESSAGE_PROMPT.format(
                    reason=reason, conversation=_format_conversation(messages),
                ),
            }],
            stream=False,
            request_timeout=LLM_TIMEOUT,
        )
        return (result.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("handoff message fell back to the fixed sentence (%s)", type(e).__name__)
        return ""


async def summarize_for_handoff(
    messages: list,
    reason: str,
    actions_taken: list = None,
    retrieved_context: list = None,
) -> str:
    """LLM-written handoff briefing for the human specialist (internal-facing,
    never shown to the customer). One call per escalation — escalations are
    rare and terminal, so the added latency is acceptable.

    Decided 2026-09-04: the summary is a synthesis of the CONVERSATION only —
    the specialist has full account access, so no data extraction belongs
    here. Falls back to the deterministic build_context_summary() on any LLM
    error (if the LLM is down, escalations are exactly what still must work).
    """
    convo = _format_conversation(messages)
    try:
        result = await litellm.acompletion(
            model=settings.litellm_classifier_model,
            messages=[{
                "role": "user",
                "content": ESCALATION_SUMMARY_PROMPT.format(reason=reason, conversation=convo),
            }],
            stream=False,
            request_timeout=LLM_TIMEOUT,
        )
        text = (result.choices[0].message.content or "").strip()
        if text:
            return f"{text}\nEscalation reason: {reason}"
    except Exception as e:
        logger.warning("summarize_for_handoff fell back to deterministic summary (%s)", type(e).__name__)
    return build_context_summary(
        messages=messages or [],
        actions_taken=actions_taken or [],
        retrieved_context=retrieved_context or [],
        reason=reason,
    )


def _result_guidance_notes(action_results: list) -> "list[str]":
    """Select the owner-authored presentation snippets that apply to THIS
    turn's results (see prompts/response_guidance.yaml for the keying rules).

    This is the progressive-disclosure replacement for the per-code stanzas
    that used to live in response_prompt: code selects, the owner authors.
    """
    guidance = get_response_guidance()
    keys: list = []

    def add(key):
        if key in guidance and key not in keys:
            keys.append(key)

    last_index = len(action_results) - 1
    for index, r in enumerate(action_results):
        if not isinstance(r, dict):
            continue
        reason = r.get("reason")
        # "Which order?" is only live if nothing ran after it this turn: a
        # later tool (e.g. the eligibility check the loop chains to) has
        # already settled it, and the note would make the agent ask anyway.
        if reason == "order_id_required" and index != last_index:
            continue
        if isinstance(reason, str):
            add(reason)
        if r.get("requires_escalation"):
            add("requires_escalation")
        if r.get("error") or r.get("unhandled_error"):
            add("tool_error")
    return [guidance[k] for k in keys]


def _build_context_section(
    state: AgentState,
    with_result_guidance: bool = False,
    small_talk_warning: bool = False,
    with_orders: bool = True,
) -> str:
    chunks = state.get("retrieved_context") or []
    action_results = state.get("action_results") or []
    customer_context = state.get("customer_context") or {}
    parts = []

    if customer_context.get("name"):
        parts.append(f"\nCustomer: {customer_context['name']}")
    # The loop-decision step gets no order list (with_orders=False): with it,
    # it picked an order_id itself instead of letting the tool/customer choose.
    orders_block = format_recent_orders(customer_context) if with_orders else ""
    if orders_block:
        parts.append(orders_block)

    if chunks:
        kb_text = "\n\n---\n\n".join(
            f"Source: {c['title']} ({c['category']})\n{c['chunk_text']}"
            for c in chunks
        )
        parts.append(f"\nKnowledge Base:\n{kb_text}")
    if action_results:
        parts.append(f"\nAction Results:\n{json.dumps(action_results, indent=2)}")
        if with_result_guidance:
            notes = _result_guidance_notes(action_results)
            if notes:
                parts.append("\nHow to present these results:\n" + "\n".join(notes))
    if small_talk_warning:
        # Owner-authored snippet (response_guidance.yaml); code only selects it.
        note = get_response_guidance().get("small_talk_warning")
        if note:
            parts.append("\nFor this reply:\n" + note)
    return "\n".join(parts)


async def _classify_intent(state: AgentState, config: dict = None) -> tuple[str, float, dict, str]:
    """
    Call LLM to classify the customer's latest message.
    Returns (intent, confidence, action_details, reasoning).
    action_details is {"tool": ..., "params": {...}} for action_request, {} otherwise.
    reasoning is non-empty only when debug_classifier is set in configurable.
    """
    debug = bool((config or {}).get("configurable", {}).get("debug_classifier"))
    raw = ""
    try:
        role_map = {"customer": "user", "agent": "assistant"}
        history_turns = [
            {"role": role_map[m["role"]], "content": m["content"]}
            for m in state["messages"][-10:]
            if m["role"] in role_map
        ]
        # Static prompt block carries a cache breakpoint: the intent prompt is
        # identical on every call, so repeat turns read it from the prompt cache
        # (cache reads bill ~0.1x and don't count toward input-token rate limits).
        # The debug suffix goes in a separate block AFTER the breakpoint so
        # enabling it doesn't invalidate the cached prefix.
        system_blocks = [
            {"type": "text", "text": INTENT_PROMPT, "cache_control": {"type": "ephemeral"}},
        ]
        # Account holder's name, after the cache breakpoint (varies per customer).
        # Lets the classifier tell when the person writing is not the account
        # holder ("I'm not Triss") — an escalation_request with trigger "decided".
        holder = ((state.get("customer_context") or {}).get("name") or "").strip()
        if holder:
            system_blocks.append({"type": "text", "text": f"\nAccount holder: {holder}"})
        # Same recent-orders block the response agent sees, so "my blender"
        # can resolve to an order_id when exactly one order matches.
        orders_block = format_recent_orders(state.get("customer_context"), identity_only=True)
        if orders_block:
            system_blocks.append({"type": "text", "text": "\n" + orders_block})
        if debug:
            system_blocks.append({
                "type": "text",
                "text": "\nInclude a 'reasoning' field explaining your classification in 1-2 sentences.",
            })
        result = await litellm.acompletion(
            model=settings.litellm_classifier_model,
            messages=[
                {"role": "system", "content": system_blocks},
                *history_turns,
            ],
            stream=False,
            request_timeout=LLM_TIMEOUT,
        )
        raw = result.choices[0].message.content.strip()
        parsed = extract_json_object(raw, caller="_classify_intent")
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
        if intent == "escalation_request":
            # trigger disambiguates the reasons that share this intent as their
            # detector: "threat", "decided" (any other classifier judgment, e.g.
            # not the account holder) vs explicit human request. Anything else
            # (missing, garbage) falls back to customer_requested — the
            # classifier forgetting the field can never produce a worse label
            # than the pre-trigger behavior.
            action_details = {"trigger": parsed.get("trigger", "")}
        reasoning = parsed.get("reasoning", "") if debug else ""
        return intent, confidence, action_details, reasoning
    except Exception as e:
        logger.warning("_classify_intent parse failure (%s): raw=%r", type(e).__name__, raw)
        return "general", 0.5, {}, ""


async def _generate_response(state: AgentState, small_talk_warning: bool = False) -> str:
    """Generate the customer-facing response from conversation history + service results."""
    context_section = _build_context_section(
        state, with_result_guidance=True, small_talk_warning=small_talk_warning,
    )
    system_content = RESPONSE_PROMPT.format(context_section=context_section)

    messages_for_llm = [{"role": "system", "content": system_content}]
    role_map = {"customer": "user", "agent": "assistant"}
    for msg in state["messages"][-settings.max_context_messages:]:
        if msg["role"] in role_map:
            messages_for_llm.append({"role": role_map[msg["role"]], "content": msg["content"]})

    try:
        result = await litellm.acompletion(
            model=settings.litellm_model,
            messages=messages_for_llm,
            stream=False,
            num_retries=3,
            request_timeout=LLM_TIMEOUT,
        )
        return result.choices[0].message.content
    except Exception:
        return "I'm having trouble processing your request right now. Please try again in a moment."


async def _generate_redirect(block_count: int, category: str, customer_message: str) -> str:
    """Generate a natural redirect message when the input guard blocks a message.

    Uses block_count (1 or 2) and category (off_topic/provocative/prompt_injection)
    to apply the correct tone per the guidelines in REDIRECT_PROMPT. The
    customer's message goes in as the user turn so the redirect can respond to
    what was actually said — and because Anthropic rejects a request with no
    user message (before this, every redirect silently hit the fallback).
    Falls back to a generic safe message on any LLM error.
    """
    system = REDIRECT_PROMPT.format(block_count=block_count, category=category)
    try:
        result = await litellm.acompletion(
            model=settings.litellm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": customer_message or "(empty message)"},
            ],
            stream=False,
            request_timeout=LLM_TIMEOUT,
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
    try:
        result = await litellm.acompletion(
            model=settings.litellm_model,
            messages=messages_for_llm,
            stream=False,
            num_retries=3,
            request_timeout=LLM_TIMEOUT,
        )
        return result.choices[0].message.content
    except Exception:
        return "I'm sorry to hear you're frustrated. Could you tell me more about what happened so I can help?"


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

    # Structural terminal: a pending confirmation must reach the customer this
    # turn. Without this, the loop-decision LLM could re-invoke the same write
    # tool in the same turn — the gate would see its own confirmation entry in
    # actions_taken and execute without the customer ever seeing the question.
    # This preserves the documented 2-turn minimum on every state change.
    if last.get("confirmation_required"):
        return False

    # Structural terminal: the tool couldn't tell which order the customer
    # means. The customer chooses; the loop must not pick one.
    if last.get("candidate_orders"):
        return False

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
    context_section = _build_context_section(state, with_orders=False)
    last_message = next(
        (m["content"] for m in reversed(state["messages"]) if m["role"] == "customer"),
        "",
    )
    raw = ""
    try:
        result = await litellm.acompletion(
            model=settings.litellm_classifier_model,
            messages=[
                {"role": "system", "content": LOOP_DECISION_PROMPT.replace("{context_section}", context_section)},
                {"role": "user", "content": last_message},
            ],
            stream=False,
            request_timeout=LLM_TIMEOUT,
        )
        raw = result.choices[0].message.content.strip()
        return extract_json_object(raw, caller="_classify_next_step")
    except Exception as e:
        # Falling back to "respond" is fail-safe (the agent answers from the
        # results it already has) but must never be silent — a quiet collapse
        # here degrades multi-step turns with no trace.
        logger.warning("_classify_next_step fallback to respond (%s): raw=%r", type(e).__name__, raw[:500])
        return {"next": "respond"}


async def _do_escalate(
    reason: str,
    state: AgentState,
    config: dict,
    confidence: "float | None" = None,
    detail: str = "",
) -> dict:
    """
    Call the escalation handler inline and return the full state update.

    Callers may spread extra fields (e.g. confidence, consecutive_blocks) over
    the returned dict to supply turn-specific values.

    confidence: value stored in the Escalation row. Pass it explicitly on
        pass-1 paths — the classifier's confidence is a local there, and
        state still holds the initial 0.0 when the row is written.
    detail: sub-reason appended to the context summary's reason line (e.g. the
        output guard's specific failure_type behind "output_guard_block").
    """
    _db = config.get("configurable", {}).get("db")
    _conv_id = config.get("configurable", {}).get("conversation_id", "")
    # Internal briefing and customer-facing handoff sentence are independent —
    # run them concurrently so the handoff message adds no latency.
    summary, handoff_message = await asyncio.gather(
        summarize_for_handoff(
            messages=state.get("messages") or [],
            reason=f"{reason} ({detail})" if detail else reason,
            actions_taken=state.get("actions_taken") or [],
            retrieved_context=state.get("retrieved_context") or [],
        ),
        _generate_handoff_message(state.get("messages") or [], reason),
    )
    context = {
        "db": _db,
        "conversation_id": _conv_id,
        "confidence": confidence if confidence is not None else state.get("confidence", 0.0),
        "messages": state.get("messages") or [],
        "context_summary": summary,
    }
    handoff = await handle_escalation(reason, context)
    return {
        "response": handoff_message or handoff,
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
    """Central customer-facing LangGraph node (see _conversation_agent_turn).

    Also owns the small-talk streak reset: any pass-1 turn that got past the
    input guard and was classified as something other than small_talk ends
    the streak. Keyed on inferred_intent, which every post-guard pass-1 branch
    sets and no blocked branch does — so new branches reset it automatically
    and blocked turns leave the streak as it was.
    """
    service_ran = bool(state.get("actions_taken"))
    update = await _conversation_agent_turn(state, config)
    if not service_ran and update.get("inferred_intent") not in (None, "", "small_talk"):
        update["consecutive_small_talk"] = 0
    return update


async def _conversation_agent_turn(state: AgentState, config: dict) -> dict:
    """
    Central customer-facing LangGraph node logic.

    Pass 1 — no service results yet: classify intent and route to a service,
              or respond directly if no service is needed.
    Pass 2 — service results available: optionally loop for another service call,
              then generate the customer-facing response.
    """
    # Pass 2 if any service has already run (actions_taken is populated by services).
    # Checking actions_taken is more reliable than checking retrieved_context/action_results
    # because those can be empty lists even after a service ran (e.g. no KB chunks found).
    service_ran = bool(state.get("actions_taken"))

    # Output-guard bypass — only the eval endpoint can set disable_output_guard
    # (chat.py test mode); the production stream path never does, so the guard
    # always runs live for real customers.
    _guard_bypass = bool(config.get("configurable", {}).get("disable_output_guard"))

    # Pass 1: intent classification
    if not service_ran:
        last_message = next(
            (m["content"] for m in reversed(state["messages"]) if m["role"] == "customer"),
            "",
        )
        # Input guard and intent classification are independent — run them
        # concurrently to cut turn latency by one full LLM round trip. When the
        # guard blocks, the classification result is discarded (a wasted call on
        # the rare blocked message, a saved round trip on every normal one).
        guard, classified = await asyncio.gather(
            check_input(last_message),
            _classify_intent(state, config),
        )
        if not guard["safe"]:
            # Persist audit log for blocked attempts (skip in test mode where conversation_id is "")
            _db = config.get("configurable", {}).get("db")
            _conv_id = config.get("configurable", {}).get("conversation_id", "")
            if _db and _conv_id:
                await log_blocked_attempt(_db, _conv_id, last_message, guard)
                await _db.commit()

            # Abusive / harmful → immediate escalation, bypasses the block counter
            # entirely (confidence 1.0 — the trigger is a deterministic guard verdict)
            if guard.get("reason") in ("abusive", "harmful"):
                reason = "abusive_input" if guard.get("reason") == "abusive" else "harmful_input"
                return {
                    **await _do_escalate(reason, state, config, confidence=1.0),
                    "confidence": 1.0,
                    "consecutive_blocks": state.get("consecutive_blocks", 0),
                    "service_call_count": 0,
                }

            new_blocks = state.get("consecutive_blocks", 0) + 1
            block_counts = {"consecutive_blocks": new_blocks}

            # Provocative → its own per-conversation counter: the 1st gets a
            # redirect that warns, the 2nd escalates. It still counts as a
            # block, so mixing it with off_topic hits the 3-in-a-row rule too.
            if guard.get("reason") == "provocative":
                provocative = state.get("provocative_count", 0) + 1
                block_counts["provocative_count"] = provocative
                if provocative >= PROVOCATIVE_ESCALATE:
                    return {
                        **await _do_escalate("provocative_input", state, config, confidence=1.0),
                        "confidence": 1.0,
                        **block_counts,
                        "service_call_count": 0,
                    }

            # 3rd consecutive block → escalate to human
            if new_blocks >= 3:
                return {
                    **await _do_escalate("repeated_blocks", state, config, confidence=1.0),
                    "confidence": 1.0,
                    **block_counts,
                    "service_call_count": 0,
                }

            # Otherwise → LLM-generated redirect with category- and count-specific tone
            redirect = await _generate_redirect(
                new_blocks, guard.get("reason", "prompt_injection"), last_message,
            )
            return {
                "response": redirect,
                "pending_service": "",
                "confidence": 1.0,
                **block_counts,
                "last_clarification_source": "",
                "service_call_count": 0,
            }

        # Message passed the guard — reset the consecutive block counter
        emotion = guard.get("emotion", "")
        intent, confidence, action_details, classifier_reasoning = classified

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
            out_guard = await check_output(question, state, bypass=_guard_bypass)
            if out_guard["safe"]:
                return {
                    "response": question,
                    "confidence": confidence,
                    "inferred_intent": intent,
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
                **await _do_escalate("unable_to_clarify", state, config, confidence=confidence),
                "confidence": confidence,
                "inferred_intent": intent,
                "consecutive_blocks": 0,
                "service_call_count": 0,
            }

        if intent == "knowledge_query":
            return {
                "pending_service": "knowledge",
                "confidence": confidence,
                "inferred_intent": intent,
                "last_clarification_source": "",
                "consecutive_blocks": 0,
                "service_call_count": 1,
                "classifier_reasoning": classifier_reasoning,
            }

        if intent == "action_request":
            return {
                "pending_service": "action",
                "pending_action": action_details,
                "confidence": confidence,
                "inferred_intent": intent,
                "last_clarification_source": "",
                "consecutive_blocks": 0,
                "service_call_count": 1,
                "classifier_reasoning": classifier_reasoning,
            }

        if intent == "escalation_request":
            esc_reason = {
                "threat": "threat",
                "decided": "classifier_decided",
            }.get(action_details.get("trigger"), "customer_requested")
            return {
                **await _do_escalate(esc_reason, state, config, confidence=confidence),
                "confidence": confidence,
                "inferred_intent": intent,
                "consecutive_blocks": 0,
                "service_call_count": 0,
            }

        if intent == "needs_clarification":
            # Cap: if we already asked a clarifying question this conversation (any source),
            # escalate instead of asking again.
            if state.get("last_clarification_source", ""):
                return {
                    **await _do_escalate("unable_to_clarify", state, config, confidence=confidence),
                    "confidence": confidence,
                    "inferred_intent": intent,
                    "consecutive_blocks": 0,
                    "service_call_count": 0,
                }
            clarification_question = action_details.get(
                "clarification_prompt",
                "Could you provide a bit more detail so I can help you?",
            )
            out_guard = await check_output(clarification_question, state, bypass=_guard_bypass)
            if out_guard["safe"]:
                return {
                    "response": clarification_question,
                    "confidence": confidence,
                    "inferred_intent": intent,
                    "pending_service": "",
                    "last_clarification_source": "intent",
                    "consecutive_blocks": 0,
                    "service_call_count": 0,
                    "classifier_reasoning": classifier_reasoning,
                }
            # Output guard blocked the clarifying question — log and escalate
            _db = config.get("configurable", {}).get("db")
            _conv_id = config.get("configurable", {}).get("conversation_id", "")
            if _db and _conv_id:
                await log_output_guard_blocked(_db, _conv_id, clarification_question, out_guard["reason"])
                await _db.commit()
            return {
                **await _do_escalate("unable_to_clarify", state, config, confidence=confidence),
                "inferred_intent": intent,
                "confidence": confidence,
                "consecutive_blocks": 0,
                "service_call_count": 0,
            }

        # small_talk — counted; past the limits it escalates without generating
        # a response. Below them it is answered exactly like general, with a
        # friendly warning on the 2nd-in-a-row / 4th-in-total turn.
        small_talk_counts: dict = {}
        small_talk_warning = False
        if intent == "small_talk":
            consecutive = state.get("consecutive_small_talk", 0) + 1
            total = state.get("total_small_talk", 0) + 1
            small_talk_counts = {"consecutive_small_talk": consecutive, "total_small_talk": total}
            if consecutive >= SMALL_TALK_ESCALATE_CONSECUTIVE or total >= SMALL_TALK_ESCALATE_TOTAL:
                return {
                    **await _do_escalate("excessive_small_talk", state, config, confidence=confidence),
                    "confidence": confidence,
                    "inferred_intent": intent,
                    "consecutive_blocks": 0,
                    "service_call_count": 0,
                    **small_talk_counts,
                }
            small_talk_warning = (
                consecutive == SMALL_TALK_WARN_CONSECUTIVE or total == SMALL_TALK_WARN_TOTAL
            )

        # general / small_talk — answer directly without a service call
        response = await _generate_response(state, small_talk_warning=small_talk_warning)
        out_guard = await check_output(response, state, bypass=_guard_bypass)
        if not out_guard["safe"]:
            _db = config.get("configurable", {}).get("db")
            _conv_id = config.get("configurable", {}).get("conversation_id", "")
            if _db and _conv_id:
                await log_output_guard_blocked(_db, _conv_id, response, out_guard["reason"])
                await _db.commit()
            # Canonical reason for triage/dashboards; the specific failure type
            # (unsupported_claim, hallucinated_id, ...) is preserved in the
            # context summary and the output_guard audit log entry.
            _reason, _detail = (
                ("guard_error", "") if out_guard["reason"] == "guard_error"
                else ("output_guard_block", out_guard["reason"])
            )
            return {
                **await _do_escalate(_reason, state, config, confidence=confidence, detail=_detail),
                "confidence": confidence,
                "inferred_intent": intent,
                "consecutive_blocks": 0,
                "service_call_count": 0,
                **small_talk_counts,
            }
        return {
            "response": response,
            "confidence": confidence,
            "inferred_intent": intent,
            "pending_service": "",
            "last_clarification_source": "",
            "consecutive_blocks": 0,
            "service_call_count": 0,
            **small_talk_counts,
        }

    # Pass 2: service results are available.
    retrieved = state.get("retrieved_context") or []
    # Minimal floor: below 0.15 indicates no meaningful match.
    # Pass empty context to the LLM rather than irrelevant chunks.
    # Hybrid search sets similarity=None for keyword-only chunks — those matched
    # exact terms, so they are kept (and `None < 0.15` would raise on Py3.9).
    if retrieved and retrieved[0].get("similarity") is not None and retrieved[0]["similarity"] < 0.15:
        retrieved = []

    # Unhandled action errors (DB failures, timeouts, unexpected exceptions) — escalate
    # immediately rather than letting the LLM improvise from incomplete data.
    # Structured rejections (ineligible, confirmation_required, requires_escalation) do
    # not set unhandled_error and are handled normally by the response generator.
    action_results = state.get("action_results") or []
    if action_results and action_results[-1].get("unhandled_error"):
        return {
            **await _do_escalate("unhandled_error", state, config),
            "confidence": state.get("confidence", 0.0),
        }

    # Structural check: if the last action result carries check_kb: True and KB hasn't been
    # queried yet this turn, route to knowledge_service immediately — no LLM decision needed.
    # This fires for defective/damaged item claims where the action tool signals that KB
    # policy content should be retrieved before the agent responds or escalates.
    service_call_count = state.get("service_call_count", 0)
    if (
        action_results
        and action_results[-1].get("check_kb")
        and not state.get("retrieved_context")
        and service_call_count < settings.service_call_limit
    ):
        return {
            "pending_service": "knowledge",
            "service_call_count": service_call_count + 1,
        }

    # Loop decision: if the last service result signals more may be needed and we're under
    # the call limit, ask the LLM whether to make another call before responding.
    # Clean successes (no error, no rejection reason, no available_action) skip this entirely.
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

    # Generate the customer-facing response using service results.
    # Pass a local state copy so the 0.15 floor is respected by _build_context_section.
    response = await _generate_response({**state, "retrieved_context": retrieved})

    _mock_handoff = config.get("configurable", {}).get("mock_handoff_intent", False)
    out_guard = await check_output(
        response,
        {**state, "retrieved_context": retrieved},
        bypass=_guard_bypass,
        mock_handoff_intent=_mock_handoff,
    )
    if not out_guard["safe"]:
        _db = config.get("configurable", {}).get("db")
        _conv_id = config.get("configurable", {}).get("conversation_id", "")
        if _db and _conv_id:
            await log_output_guard_blocked(_db, _conv_id, response, out_guard["reason"])
            await _db.commit()
        # Canonical reason for triage/dashboards; specific failure type kept as
        # detail (see the matching pass-1 site).
        _reason, _detail = (
            ("guard_error", "") if out_guard["reason"] == "guard_error"
            else ("output_guard_block", out_guard["reason"])
        )
        return {
            **await _do_escalate(_reason, state, config, detail=_detail),
            "confidence": state.get("confidence", 0.0),
        }

    # Escalation after a passed guard — two signals, checked in priority order.
    # Either way the rich LLM response is kept (it carries the KB policy
    # details the customer needs) and the backend side effects (DB record,
    # conversation status) fire here; only the recorded reason differs.
    #
    # 1. Tool-signalled: any action result this turn carries
    #    requires_escalation=True (defective, duplicate_item,
    #    carrier_claim_eligible). Scans the whole turn — not just the last
    #    result — so a later hop (KB follow-up, loop re-invocation, KB failure)
    #    cannot bury the flag. Checked FIRST: the requires_escalation guidance
    #    tells the agent to end with a handoff, so the guard almost always
    #    detects handoff_intent too — when handoff detection ran first, every
    #    tool escalation was filed as llm_escalation_intent (UI test 2026-11-04).
    # 2. Output-guard handoff detection: the agent is handing off on its own.
    #    Structural split of that catch-all: KB searched this turn and empty
    #    (post-floor) → no_information; otherwise llm_escalation_intent.
    # Other escalation paths (abusive input, output guard block, repeated
    # failures, customer request) use _do_escalate() and never reach this point.
    esc_entry = next(
        (r for r in reversed(action_results)
         if isinstance(r, dict) and r.get("requires_escalation")),
        None,
    )
    esc_reason = ""
    if esc_entry is not None:
        esc_reason = esc_entry.get("reason") or "defective"
    elif out_guard.get("handoff_intent"):
        kb_searched = any(
            e.get("service") == "knowledge_service"
            for e in (state.get("actions_taken") or [])
        )
        esc_reason = "no_information" if (kb_searched and not retrieved) else "llm_escalation_intent"

    if esc_reason:
        _db = config.get("configurable", {}).get("db")
        _conv_id = config.get("configurable", {}).get("conversation_id", "")
        summary = await summarize_for_handoff(
            messages=state.get("messages") or [],
            reason=esc_reason,
            actions_taken=state.get("actions_taken") or [],
            retrieved_context=retrieved,
        )
        await run_escalation_side_effects(esc_reason, {
            "db": _db,
            "conversation_id": _conv_id,
            "confidence": state.get("confidence", 0.0),
            "messages": state.get("messages") or [],
            "context_summary": summary,
        })
        return {
            "response": response,
            "requires_escalation": True,
            "escalation_reason": esc_reason,
            "context_summary": summary,
            "confidence": state.get("confidence", 0.0),
            "pending_service": "",
            "last_clarification_source": "",
            "actions_taken": (state.get("actions_taken") or []) + [
                {"service": "escalation_handler", "action": "escalate", "reason": esc_reason}
            ],
        }

    return {
        "response": response,
        "confidence": state.get("confidence", 0.0),
        "pending_service": "",
        "last_clarification_source": "",
    }
