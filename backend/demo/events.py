"""Pipeline events for one agent turn — the structure the demo's behind-the-
scenes panel renders, persisted in ``turns.events`` and sent over SSE.

Event shapes (``type`` first):
  input_guard    {verdict: safe|blocked, reason, strike}
  tool_call      {tool, result, success, order_id, confirmation_required,
                  requires_escalation, duration_ms}
  kb_retrieval   {query, articles: [title], chunks, top_similarity, success}
  output_guard   {verdict: pass|flagged, reason}
  escalation     {reason, context_summary}
  error          {kind, message}

Batched derivation (this module) reads the graph's final output plus the
audit rows the guards wrote during the turn. It cannot see step timings or
guard verdicts on paths that leave no trace — the progressive slice adds
those by emitting from inside the pipeline. Everything derived here is
structural: nothing is inferred from response text.
"""
from typing import Optional

# Escalation paths where the LLM's response was kept (the output guard passed
# and the escalation side-effects fired separately). Every other escalation
# reason replaces the response with a handoff message that is not guarded.
_RESPONSE_KEPT_ESCALATIONS = frozenset({
    "llm_escalation_intent", "defective", "duplicate_item", "carrier_claim_eligible",
})

# Similarity floor the conversation agent applies before using KB chunks.
_KB_SIMILARITY_FLOOR = 0.15


def derive_events(final_output: dict, audit_rows: list, retrieved_context: "list | None" = None) -> list:
    """Build the ordered event list for a completed turn.

    audit_rows: AuditLog rows written during this turn (input_guard_blocked /
    output_guard_blocked), as objects with ``action`` and ``output_data``.
    """
    final_output = final_output or {}
    events: list = []

    input_block = next((r for r in audit_rows if r.action == "input_guard_blocked"), None)
    if input_block:
        events.append({
            "type": "input_guard",
            "verdict": "blocked",
            "reason": (input_block.output_data or {}).get("category", "unknown"),
            "strike": final_output.get("consecutive_blocks"),
        })
    else:
        events.append({"type": "input_guard", "verdict": "safe", "reason": None, "strike": None})

    actions = final_output.get("actions_taken") or []
    results = [
        r for r in (final_output.get("action_results") or [])
        if isinstance(r, dict) and not str(r.get("error", "")).startswith("Knowledge search failed")
    ]
    retrieved = retrieved_context if retrieved_context is not None else (final_output.get("retrieved_context") or [])
    kb_entries = [a for a in actions if a.get("service") == "knowledge_service"]
    result_i = 0
    for a in actions:
        service = a.get("service")
        if service == "action_service":
            result = results[result_i] if result_i < len(results) else {}
            result_i += 1
            # The tool's own reason code ("eligible", "already_shipped", …) is
            # more telling than the coarse result_detail; confirmation gates
            # keep their explicit marker.
            detail = a.get("result_detail") or ("success" if a.get("success") else "failed")
            reason = result.get("reason")
            if detail != "confirmation_pending" and isinstance(reason, str) and reason:
                detail = reason
            events.append({
                "type": "tool_call",
                "tool": a.get("action") or "unknown",
                "result": detail,
                "success": bool(a.get("success")),
                "order_id": a.get("order_id"),
                "confirmation_required": bool(a.get("confirmation_required")),
                "requires_escalation": bool(result.get("requires_escalation")),
                "duration_ms": a.get("duration_ms"),
            })
        elif service == "knowledge_service":
            top = a.get("top_similarity")
            articles: Optional[list] = None
            # Only the last search's chunks survive in state; earlier ones keep
            # their count but not their titles.
            if a is kb_entries[-1]:
                usable = retrieved
                if usable and usable[0].get("similarity") is not None and usable[0]["similarity"] < _KB_SIMILARITY_FLOOR:
                    usable = []
                seen: list = []
                for chunk in usable:
                    title = chunk.get("title")
                    if title and title not in seen:
                        seen.append(title)
                articles = seen
            events.append({
                "type": "kb_retrieval",
                "query": a.get("query", ""),
                "articles": articles,
                "chunks": int(a.get("chunks_retrieved") or 0),
                "top_similarity": top,
                "success": a.get("success", True) is not False,
            })

    escalation_reason = final_output.get("escalation_reason") or ""
    requires_escalation = bool(final_output.get("requires_escalation"))

    output_block = next((r for r in audit_rows if r.action == "output_guard_blocked"), None)
    if output_block:
        events.append({
            "type": "output_guard",
            "verdict": "flagged",
            "reason": (output_block.output_data or {}).get("failure_reason", "unknown"),
        })
    elif (
        final_output.get("response")
        and not input_block
        and (not requires_escalation or escalation_reason in _RESPONSE_KEPT_ESCALATIONS)
    ):
        events.append({"type": "output_guard", "verdict": "pass", "reason": None})

    if requires_escalation:
        events.append({
            "type": "escalation",
            "reason": escalation_reason or "unknown",
            "context_summary": final_output.get("context_summary") or "",
        })
    return events


def error_events(kind: str, message: str) -> list:
    return [{"type": "error", "kind": kind, "message": message}]


def turn_payload(index: int, events: list, latency_ms: int, usage: "dict | None",
                 final_output: "dict | None" = None, error: "str | None" = None) -> dict:
    """The record sent as the ``turn`` / ``turn_complete`` SSE events and
    persisted to ``turns``."""
    final_output = final_output or {}
    usage = usage or {}
    return {
        "index": index,
        "events": events,
        "latency_ms": latency_ms,
        "llm_calls": usage.get("llm_calls"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cost_usd": usage.get("cost_usd"),
        "cost_basis": usage.get("cost_basis", "unavailable"),
        "requires_escalation": bool(final_output.get("requires_escalation")),
        "escalation_reason": final_output.get("escalation_reason") or None,
        "context_summary": final_output.get("context_summary") or None,
        "error": error,
    }
