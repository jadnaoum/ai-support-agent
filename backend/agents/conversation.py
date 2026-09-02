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

    for r in action_results:
        if not isinstance(r, dict):
            continue
        reason = r.get("reason")
        if isinstance(reason, str):
            add(reason)
        if r.get("requires_escalation"):
            add("requires_escalation")
        if r.get("error") or r.get("unhandled_error"):
            add("tool_error")
    return [guidance[k] for k in keys]


def _build_context_section(state: AgentState, with_result_guidance: bool = False) -> str:
    chunks = state.get("retrieved_context") or []
    action_results = state.get("action_results") or []
    customer_context = state.get("customer_context") or {}
    parts = []

    if customer_context.get("name"):
        parts.append(f"\nCustomer: {customer_context['name']}")

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
        reasoning = parsed.get("reasoning", "") if debug else ""
        return intent, confidence, action_details, reasoning
    except Exception as e:
        logger.warning("_classify_intent parse failure (%s): raw=%r", type(e).__name__, raw)
        return "general", 0.5, {}, ""


async def _generate_response(state: AgentState) -> str:
    """Generate the customer-facing response from conversation history + service results."""
    context_section = _build_context_section(state, with_result_guidance=True)
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
    summary = build_context_summary(
        messages=state.get("messages") or [],
        actions_taken=state.get("actions_taken") or [],
        retrieved_context=state.get("retrieved_context") or [],
        reason=f"{reason} ({detail})" if detail else reason,
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

            # Abusive → immediate escalation, bypasses the block counter entirely
            # (confidence 1.0 — the trigger is a deterministic guard verdict)
            if guard.get("reason") == "abusive":
                return {
                    **await _do_escalate("abusive_input", state, config, confidence=1.0),
                    "confidence": 1.0,
                    "consecutive_blocks": state.get("consecutive_blocks", 0),
                    "service_call_count": 0,
                }

            new_blocks = state.get("consecutive_blocks", 0) + 1

            # 3rd consecutive block → escalate to human
            if new_blocks >= 3:
                return {
                    **await _do_escalate("repeated_blocks", state, config, confidence=1.0),
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
            return {
                **await _do_escalate("customer_requested", state, config, confidence=confidence),
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

        # general — answer directly without a service call
        response = await _generate_response(state)
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
            }
        return {
            "response": response,
            "confidence": confidence,
            "inferred_intent": intent,
            "pending_service": "",
            "last_clarification_source": "",
            "consecutive_blocks": 0,
            "service_call_count": 0,
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

    # Output-guard handoff detection: guard passed AND detected the agent is
    # handing the conversation off to a human. Keep the rich LLM response —
    # it may contain KB policy details the customer needs — and fire backend
    # side effects (DB record, conversation status) the same way the
    # tool-signalled path does.
    if out_guard.get("handoff_intent"):
        esc_reason = "llm_escalation_intent"
        _db = config.get("configurable", {}).get("db")
        _conv_id = config.get("configurable", {}).get("conversation_id", "")
        summary = build_context_summary(
            messages=state.get("messages") or [],
            actions_taken=state.get("actions_taken") or [],
            retrieved_context=retrieved,
            reason=esc_reason,
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

    # Tool-signalled escalation: any action result this turn carries
    # requires_escalation=True (e.g. defective/damaged item claims). Scanning the
    # whole turn — not just the last result — so a later hop (KB follow-up, loop
    # re-invocation, KB failure) cannot bury the flag and silently drop a
    # designed-as-structural escalation. The LLM has already generated a KB-rich
    # response with policy details and handoff language — keep that response and
    # fire the backend side effects (DB record, conversation status) separately.
    # Other escalation paths (abusive input, output guard, repeated failures,
    # customer request) use _do_escalate() directly above and never reach this
    # point; the handoff-detection branch above returns, so this cannot double-fire.
    esc_entry = next(
        (r for r in reversed(action_results)
         if isinstance(r, dict) and r.get("requires_escalation")),
        None,
    )
    if esc_entry is not None:
        esc_reason = esc_entry.get("reason") or "defective"
        _db = config.get("configurable", {}).get("db")
        _conv_id = config.get("configurable", {}).get("conversation_id", "")
        summary = build_context_summary(
            messages=state.get("messages") or [],
            actions_taken=state.get("actions_taken") or [],
            retrieved_context=retrieved,
            reason=esc_reason,
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
