"""
Behavioral judge — sheets 4-7: KB Retrieval, Action Execution, Escalation,
Conversation Quality.

Uses LLM-as-judge with per-sheet rubrics. Sonnet by default; Opus when --calibrate.
All calls go through LiteLLM.

All async functions return:
    {"verdict": "pass"|"fail", "score": 1.0|0.0, "reasoning": str,
     "failure_reason": str|None, "cost_usd": float}
"""
import json
import os
import sys

import litellm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from evals.config import JUDGE_MODEL_BEHAVIORAL, JUDGE_MODEL_CALIBRATION  # noqa: E402
from prompts.loader import get_prompt  # noqa: E402


def _verdict(result: str, reasoning: str, failure_reason: str = None, cost_usd: float = 0.0) -> dict:
    scores = {"pass": 1.0, "fail": 0.0}
    return {
        "verdict": result,
        "score": scores.get(result, 0.0),
        "reasoning": reasoning,
        "failure_reason": failure_reason,
        "cost_usd": cost_usd,
    }


async def _llm_judge(prompt: str, calibrate: bool = False) -> dict:
    """Call LLM judge; return structured verdict with actual cost. Returns 'fail' on any error."""
    model = JUDGE_MODEL_CALIBRATION if calibrate else JUDGE_MODEL_BEHAVIORAL
    try:
        result = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            timeout=90,
            num_retries=3,
        )
        try:
            cost = litellm.completion_cost(completion_response=result)
        except Exception:
            cost = 0.0
        raw = result.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        v = parsed.get("verdict", "fail")
        # Normalise partial → fail
        if v == "partial":
            v = "fail"
        r = parsed.get("reasoning", "No reasoning provided.")
        fr = parsed.get("failure_reason") if v == "fail" else None
        scores = {"pass": 1.0, "fail": 0.0}
        return {"verdict": v, "score": scores.get(v, 0.0), "reasoning": r,
                "failure_reason": fr, "cost_usd": cost}
    except Exception as e:
        ename = type(e).__name__.lower()
        estr = str(e).lower()
        if "timeout" in ename or "timeout" in estr:
            fr = "api_timeout"
        elif "ratelimit" in ename or "rate_limit" in estr or "rate limit" in estr:
            fr = "rate_limit"
        else:
            fr = "judge_error"
        return _verdict("fail", f"Judge call failed: {e}", fr)


# ---------------------------------------------------------------------------
# KB Retrieval judge
# ---------------------------------------------------------------------------

# PROMPT — edit in prompts/eval_rubrics.yaml
_KB_PROMPT = get_prompt("kb_retrieval_prompt")


async def judge_kb_retrieval(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _KB_PROMPT.format(
        conversation=test_case.get("conversation", ""),
        reference_content=test_case.get("reference_content") or "null",
        expected_behavior=test_case.get("expected_behavior", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
        actions_taken=json.dumps(agent_response.get("actions_taken", [])),
    )
    return await _llm_judge(prompt, calibrate)


# ---------------------------------------------------------------------------
# Action Execution judge
# ---------------------------------------------------------------------------

# PROMPT — edit in prompts/eval_rubrics.yaml
_ACTION_PROMPT = get_prompt("action_execution_prompt")


async def judge_action_execution(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _ACTION_PROMPT.format(
        conversation=test_case.get("conversation", ""),
        mock_account_state=test_case.get("mock_account_state", "{}"),
        expected_tool_call=test_case.get("expected_tool_call", ""),
        expected_behavior=test_case.get("expected_behavior", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
        actions_taken=json.dumps(agent_response.get("actions_taken", [])),
        confidence=agent_response.get("confidence", 0.0),
        requires_escalation=agent_response.get("requires_escalation", False),
    )
    return await _llm_judge(prompt, calibrate)


# ---------------------------------------------------------------------------
# Escalation judge
# ---------------------------------------------------------------------------

# PROMPT — edit in prompts/eval_rubrics.yaml
_ESCALATION_PROMPT = get_prompt("escalation_prompt")


async def judge_escalation(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    # Structural reason check (hybrid design, 2026-09-03): the sheet's
    # escalation_reason column holds SYSTEM ENUM CODES (pipe-separated
    # alternatives allowed) from ESCALATION_TRIGGER_REASONS, so the reason
    # match is exact and programmatic — the LLM judge evaluates only the
    # decision, framing, and context summary. "none"/empty means no
    # escalation expected; the rubric governs that case.
    expected_raw = str(test_case.get("escalation_reason") or "").strip().lower()
    actual_reason = str(agent_response.get("escalation_reason") or "").strip()
    escalated = bool(agent_response.get("requires_escalation"))
    reason_note = expected_raw
    if expected_raw and expected_raw != "none" and escalated:
        expected_set = {t.strip() for t in expected_raw.split("|") if t.strip()}
        if actual_reason not in expected_set:
            return {
                "verdict": "fail",
                "score": 0.0,
                "reasoning": (
                    f"Structural reason check: agent escalated with reason code "
                    f"'{actual_reason}' but the expected code(s) are '{expected_raw}'. "
                    "No LLM judgment involved — exact enum comparison."
                ),
                "failure_reason": "wrong_escalation_reason",
                "cost_usd": 0.0,
            }
        reason_note = (
            f"{expected_raw} (STRUCTURALLY VERIFIED: the agent's reason code "
            f"'{actual_reason}' matches — do not judge reason labels; evaluate "
            "only the decision, the response framing, and the context summary)"
        )

    prompt = _ESCALATION_PROMPT.format(
        conversation=test_case.get("conversation", ""),
        mock_account_state=test_case.get("mock_account_state", "{}"),
        escalation_reason=reason_note,
        expected_behavior=test_case.get("expected_behavior", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
        requires_escalation=agent_response.get("requires_escalation", False),
        escalation_reason_actual=agent_response.get("escalation_reason", ""),
        actions_taken=json.dumps(agent_response.get("actions_taken", [])),
        context_summary=agent_response.get("context_summary") or "(not captured)",
    )
    return await _llm_judge(prompt, calibrate)


# ---------------------------------------------------------------------------
# Conversation Quality judge
# ---------------------------------------------------------------------------

# PROMPT — edit in prompts/eval_rubrics.yaml
_QUALITY_PROMPT = get_prompt("conversation_quality_prompt")


async def judge_conversation_quality(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _QUALITY_PROMPT.format(
        scenario_type=test_case.get("scenario_type", ""),
        tone_rubric=test_case.get("tone_rubric", ""),
        expected_behavior_summary=test_case.get("expected_behavior_summary", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
    )
    return await _llm_judge(prompt, calibrate)
