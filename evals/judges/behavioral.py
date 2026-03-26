"""
Behavioral judge — sheets 4-7: KB Retrieval, Action Execution, Escalation,
Conversation Quality.

Uses LLM-as-judge with per-sheet rubrics. Sonnet by default; Opus when --calibrate.
All calls go through LiteLLM.

All async functions return:
    {"verdict": "pass"|"partial"|"fail", "score": 1.0|0.5|0.0, "reasoning": str}
"""
import json
import os
import sys

import litellm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from evals.config import JUDGE_MODEL_BEHAVIORAL, JUDGE_MODEL_CALIBRATION  # noqa: E402


def _verdict(result: str, reasoning: str) -> dict:
    scores = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
    return {"verdict": result, "score": scores.get(result, 0.0), "reasoning": reasoning}


async def _llm_judge(prompt: str, calibrate: bool = False) -> dict:
    """Call LLM judge; return structured verdict. Returns 'partial' on any failure."""
    model = JUDGE_MODEL_CALIBRATION if calibrate else JUDGE_MODEL_BEHAVIORAL
    try:
        result = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )
        raw = result.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        v = parsed.get("verdict", "partial")
        r = parsed.get("reasoning", "No reasoning provided.")
        scores = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
        return {"verdict": v, "score": scores.get(v, 0.5), "reasoning": r}
    except Exception as e:
        return _verdict("partial", f"Judge call failed: {e}")


# ---------------------------------------------------------------------------
# KB Retrieval judge
# ---------------------------------------------------------------------------

_KB_PROMPT = """You are evaluating whether an AI customer support agent correctly retrieved and used knowledge base articles.

Test case:
- Customer conversation: {conversation}
- Available KB articles: {available_kb_articles}
- Expected article to retrieve: {expected_article}
- Expected behavior: {expected_behavior}
- Judge rubric: {judge_rubric}

Agent's actual response: {agent_response}
Services called by agent: {actions_taken}

Evaluate strictly against the rubric. Respond with JSON only:
{{"verdict": "pass"|"partial"|"fail", "reasoning": "2-3 sentences explaining your verdict"}}"""


async def judge_kb_retrieval(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _KB_PROMPT.format(
        conversation=test_case.get("conversation", ""),
        available_kb_articles=test_case.get("available_kb_articles", ""),
        expected_article=test_case.get("expected_article", ""),
        expected_behavior=test_case.get("expected_behavior", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
        actions_taken=json.dumps(agent_response.get("actions_taken", [])),
    )
    return await _llm_judge(prompt, calibrate)


# ---------------------------------------------------------------------------
# Action Execution judge
# ---------------------------------------------------------------------------

_ACTION_PROMPT = """You are evaluating whether an AI customer support agent called the correct tool with the correct arguments.

Test case:
- Customer conversation: {conversation}
- Mock account state (what the DB would return): {mock_account_state}
- Expected tool call: {expected_tool_call}
- Expected behavior: {expected_behavior}
- Judge rubric: {judge_rubric}

Agent's actual response: {agent_response}
Tools actually called (actions_taken): {actions_taken}
Agent confidence: {confidence}
Agent escalated: {requires_escalation}

Focus on: (1) was the correct tool called? (2) were the arguments correct? (3) did the agent accurately report the result?

Respond with JSON only:
{{"verdict": "pass"|"partial"|"fail", "reasoning": "2-3 sentences explaining your verdict"}}"""


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

_ESCALATION_PROMPT = """You are evaluating whether an AI customer support agent escalated correctly.

Test case:
- Customer conversation: {conversation}
- Mock account state: {mock_account_state}
- Expected escalation reason: {escalation_reason}
- Expected behavior: {expected_behavior}
- Judge rubric: {judge_rubric}

Agent's actual response: {agent_response}
Agent escalated: {requires_escalation}
Agent's escalation reason: {escalation_reason_actual}
Actions taken: {actions_taken}

Respond with JSON only:
{{"verdict": "pass"|"partial"|"fail", "reasoning": "2-3 sentences explaining your verdict"}}"""


async def judge_escalation(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _ESCALATION_PROMPT.format(
        conversation=test_case.get("conversation", ""),
        mock_account_state=test_case.get("mock_account_state", "{}"),
        escalation_reason=test_case.get("escalation_reason", ""),
        expected_behavior=test_case.get("expected_behavior", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
        requires_escalation=agent_response.get("requires_escalation", False),
        escalation_reason_actual=agent_response.get("escalation_reason", ""),
        actions_taken=json.dumps(agent_response.get("actions_taken", [])),
    )
    return await _llm_judge(prompt, calibrate)


# ---------------------------------------------------------------------------
# Conversation Quality judge
# ---------------------------------------------------------------------------

_QUALITY_PROMPT = """You are evaluating the tone and quality of an AI customer support agent's response.

Test case:
- Scenario type: {scenario_type}
- Tone rubric: {tone_rubric}
- Expected behavior summary: {expected_behavior_summary}
- Judge rubric: {judge_rubric}

Agent's actual response: {agent_response}

Focus on empathy, clarity, professionalism, and whether the tone matches the scenario.
Do NOT evaluate factual correctness — only tone and communication quality.

Respond with JSON only:
{{"verdict": "pass"|"partial"|"fail", "reasoning": "2-3 sentences on tone and quality"}}"""


async def judge_conversation_quality(test_case: dict, agent_response: dict, calibrate: bool = False) -> dict:
    prompt = _QUALITY_PROMPT.format(
        scenario_type=test_case.get("scenario_type", ""),
        tone_rubric=test_case.get("tone_rubric", ""),
        expected_behavior_summary=test_case.get("expected_behavior_summary", ""),
        judge_rubric=test_case.get("judge_rubric", ""),
        agent_response=agent_response.get("response", ""),
    )
    return await _llm_judge(prompt, calibrate)
