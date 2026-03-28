"""
Safety judge — sheets 8-11: PII & Data Leakage, Policy Compliance,
Graceful Failure, Context Retention.

Uses LLM-as-judge with per-sheet rubrics. Sonnet by default; Opus when --calibrate.

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
    model = JUDGE_MODEL_CALIBRATION if calibrate else JUDGE_MODEL_BEHAVIORAL
    try:
        result = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
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
        return _verdict("fail", f"Judge call failed: {e}")


# ---------------------------------------------------------------------------
# PII & Data Leakage judge
# ---------------------------------------------------------------------------

# PROMPT — edit in prompts/eval_rubrics.yaml
_PII_PROMPT = get_prompt("pii_leakage_prompt")


async def judge_pii_leakage(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _PII_PROMPT.format(
        conversation=test_case.get("conversation", ""),
        mock_account_state=test_case.get("mock_account_state", "{}"),
        expected_behavior=test_case.get("expected_behavior", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
    )
    return await _llm_judge(prompt, calibrate)


# ---------------------------------------------------------------------------
# Policy Compliance judge
# ---------------------------------------------------------------------------

# PROMPT — edit in prompts/eval_rubrics.yaml
_POLICY_PROMPT = get_prompt("policy_compliance_prompt")


async def judge_policy_compliance(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _POLICY_PROMPT.format(
        conversation=test_case.get("conversation", ""),
        relevant_policy=test_case.get("relevant_policy", ""),
        mock_account_state=test_case.get("mock_account_state", "{}"),
        expected_behavior=test_case.get("expected_behavior", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
        requires_escalation=agent_response.get("requires_escalation", False),
        actions_taken=json.dumps(agent_response.get("actions_taken", [])),
    )
    return await _llm_judge(prompt, calibrate)


# ---------------------------------------------------------------------------
# Graceful Failure judge
# ---------------------------------------------------------------------------

# PROMPT — edit in prompts/eval_rubrics.yaml
_GRACEFUL_FAILURE_PROMPT = get_prompt("graceful_failure_prompt")


async def judge_graceful_failure(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _GRACEFUL_FAILURE_PROMPT.format(
        conversation=test_case.get("conversation", ""),
        simulated_failure=test_case.get("simulated_failure", "{}"),
        expected_behavior=test_case.get("expected_behavior", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
        requires_escalation=agent_response.get("requires_escalation", False),
        actions_taken=json.dumps(agent_response.get("actions_taken", [])),
    )
    return await _llm_judge(prompt, calibrate)


# ---------------------------------------------------------------------------
# Context Retention judge
# ---------------------------------------------------------------------------

# PROMPT — edit in prompts/eval_rubrics.yaml
_CONTEXT_PROMPT = get_prompt("context_retention_prompt")


async def judge_context_retention(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _CONTEXT_PROMPT.format(
        conversation=test_case.get("conversation", ""),
        mock_account_state=test_case.get("mock_account_state", "{}"),
        test_focus=test_case.get("test_focus", ""),
        expected_behavior=test_case.get("expected_behavior", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
    )
    return await _llm_judge(prompt, calibrate)
