#!/usr/bin/env python3
"""
Eval runner — reads test cases from evals/eval_test_cases.xlsx, runs them
against the agent's test endpoint, judges the responses, and writes results.

Usage:
    python evals/run_evals.py --tag "v1.0" --desc "Initial baseline run"
    python evals/run_evals.py --tag "v1.1_intent" --desc "Tune intent prompt" --sheets "Intent Classifier"
    python evals/run_evals.py --tag "v1.0_calibration" --desc "Calibration baseline" --calibrate

Requirements:
    - Agent must be running with APP_ENV=test
    - ANTHROPIC_API_KEY must be set (for LLM judge calls)
    - openpyxl, requests, litellm installed
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm

from evals.config import (  # noqa: E402
    AGENT_MODEL,
    AGENT_TEST_ENDPOINT,
    AGENT_TIMEOUT,
    AVG_AGENT_COMPLETION_TOKENS,
    AVG_AGENT_PROMPT_TOKENS,
    AVG_JUDGE_COMPLETION_TOKENS,
    AVG_JUDGE_PROMPT_TOKENS,
    DEFAULT_CUSTOMER_ID,
    EVALS_DIR,
    JUDGE_MODEL_BEHAVIORAL,
    JUDGE_MODEL_CALIBRATION,
    JUDGE_MODEL_CLASSIFICATION,
    MODEL_PRICE_PER_TOKEN,
    RUN_HISTORY_SHEET,
    SHEET_CALL_PROFILE,
    SHEET_JUDGE_TYPE,
    SHEET_NAMES,
    TEST_CASES_FILE,
)

# Lazy import openpyxl so the error message is clear if it's missing
try:
    import openpyxl
    from openpyxl.styles import PatternFill, Font
except ImportError:
    sys.exit("openpyxl is required. Run: pip install openpyxl")

# Judge modules (lazy import to avoid loading LiteLLM until needed)
from evals.judges.classification import (  # noqa: E402
    judge_input_guard,
    judge_intent_classifier,
    judge_output_guard,
)
from evals.judges.behavioral import (  # noqa: E402
    judge_kb_retrieval,
    judge_action_execution,
    judge_escalation,
    judge_conversation_quality,
)
from evals.judges.safety import (  # noqa: E402
    judge_pii_leakage,
    judge_policy_compliance,
    judge_graceful_failure,
    judge_context_retention,
)


# ---------------------------------------------------------------------------
# Excel colour fills
# ---------------------------------------------------------------------------

FILL_PASS    = PatternFill("solid", fgColor="E6F4EA")  # green
FILL_PARTIAL = PatternFill("solid", fgColor="FFF3E0")  # orange
FILL_FAIL    = PatternFill("solid", fgColor="FCE4EC")  # red
FILL_HEADER  = PatternFill("solid", fgColor="E8EAF6")  # indigo-ish header
FONT_BOLD    = Font(bold=True)


# ---------------------------------------------------------------------------
# Helpers — parse conversation field
# ---------------------------------------------------------------------------

def _parse_conversation(raw) -> list:
    """
    The 'conversation' column can be:
      - A plain string (single customer message)
      - A JSON string encoding a list of {"role": ..., "content": ...} dicts

    Returns a list of {"role": ..., "content": ...} dicts always.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    raw_str = str(raw).strip()
    if raw_str.startswith("["):
        try:
            return json.loads(raw_str)
        except json.JSONDecodeError:
            pass
    # Plain string → single customer message
    return [{"role": "customer", "content": raw_str}]


def _parse_json_field(raw, default=None):
    """Parse a JSON string field; return default on failure."""
    if raw is None:
        return default if default is not None else {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def _row_to_dict(ws, row_idx: int) -> dict:
    """Read a worksheet row into a dict keyed by the header row."""
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    return {
        h: ws.cell(row_idx, c).value
        for c, h in enumerate(headers, 1)
        if h is not None
    }


# ---------------------------------------------------------------------------
# Agent call helpers
# ---------------------------------------------------------------------------

def _call_agent_full(messages: list, mock_context: dict, customer_id: str = None) -> dict:
    """POST to /api/chat/test and return parsed JSON response."""
    payload = {
        "customer_id": customer_id or DEFAULT_CUSTOMER_ID,
        "messages": messages,
        "mock_context": mock_context,
    }
    try:
        resp = requests.post(AGENT_TEST_ENDPOINT, json=payload, timeout=AGENT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"error": "Agent not reachable. Is it running with APP_ENV=test?"}
    except Exception as e:
        return {"error": str(e)}


def _call_agent_output_guard(agent_response: str, tools_called: list, known_ids: dict) -> dict:
    """POST to /api/chat/test in output guard mode."""
    payload = {
        "test_output_guard": True,
        "agent_response": agent_response,
        "tools_called": tools_called,
        "known_ids": known_ids,
    }
    try:
        resp = requests.post(AGENT_TEST_ENDPOINT, json=payload, timeout=AGENT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"error": "Agent not reachable."}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Per-sheet run logic
# ---------------------------------------------------------------------------

async def run_input_guard(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    """Returns (agent_response, judgment)."""
    messages = [{"role": "customer", "content": test_case.get("customer_message", "")}]
    agent_resp = _call_agent_full(messages, {})
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = judge_input_guard(test_case, agent_resp)
    return agent_resp, judgment


async def run_intent_classifier(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    messages = _parse_conversation(test_case.get("conversation"))
    agent_resp = _call_agent_full(messages, {})
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = judge_intent_classifier(test_case, agent_resp)
    return agent_resp, judgment


async def run_output_guard(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    agent_response_text = str(test_case.get("agent_response", ""))
    tools_called = _parse_json_field(test_case.get("tools_called"), default=[])
    known_ids = _parse_json_field(test_case.get("known_ids"), default={})

    agent_resp = _call_agent_output_guard(agent_response_text, tools_called, known_ids)
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = await judge_output_guard(test_case, agent_resp)
    return agent_resp, judgment


async def run_kb_retrieval(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    messages = _parse_conversation(test_case.get("conversation"))
    agent_resp = _call_agent_full(messages, {})
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = await judge_kb_retrieval(test_case, agent_resp, calibrate)
    return agent_resp, judgment


async def run_action_execution(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    messages = _parse_conversation(test_case.get("conversation"))
    mock_context = _parse_json_field(test_case.get("mock_account_state"))
    agent_resp = _call_agent_full(messages, mock_context)
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = await judge_action_execution(test_case, agent_resp, calibrate)
    return agent_resp, judgment


async def run_escalation(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    messages = _parse_conversation(test_case.get("conversation"))
    mock_context = _parse_json_field(test_case.get("mock_account_state"))
    agent_resp = _call_agent_full(messages, mock_context)
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = await judge_escalation(test_case, agent_resp, calibrate)
    return agent_resp, judgment


async def run_conversation_quality(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    """
    Conversation Quality cases use {{AGENT_RESPONSE}} as a placeholder.
    We send the preceding turns to the agent, capture the real response,
    then pass that response to the judge.
    """
    raw_conv = _parse_conversation(test_case.get("conversation"))
    # Split on the {{AGENT_RESPONSE}} placeholder
    pre_turns = []
    for msg in raw_conv:
        if "{{AGENT_RESPONSE}}" in str(msg.get("content", "")):
            break
        pre_turns.append(msg)

    # Only send customer turns that precede the placeholder
    messages = [m for m in pre_turns if m.get("role") == "customer"]
    if not messages:
        messages = [{"role": "customer", "content": "Hello"}]

    agent_resp = _call_agent_full(messages, {})
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = await judge_conversation_quality(test_case, agent_resp, calibrate)
    return agent_resp, judgment


async def run_pii_leakage(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    messages = _parse_conversation(test_case.get("conversation"))
    mock_context = _parse_json_field(test_case.get("mock_account_state"))
    agent_resp = _call_agent_full(messages, mock_context)
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = await judge_pii_leakage(test_case, agent_resp, calibrate)
    return agent_resp, judgment


async def run_policy_compliance(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    messages = _parse_conversation(test_case.get("conversation"))
    mock_context = _parse_json_field(test_case.get("mock_account_state"))
    agent_resp = _call_agent_full(messages, mock_context)
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = await judge_policy_compliance(test_case, agent_resp, calibrate)
    return agent_resp, judgment


async def run_graceful_failure(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    # Note: simulated_failure is metadata for the judge — the test endpoint
    # doesn't inject failures at the tool level. The judge evaluates honesty
    # about failures that occur naturally (e.g. order not found in DB).
    messages = _parse_conversation(test_case.get("conversation"))
    agent_resp = _call_agent_full(messages, {})
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = await judge_graceful_failure(test_case, agent_resp, calibrate)
    return agent_resp, judgment


async def run_context_retention(test_case: dict, calibrate: bool) -> tuple[dict, dict]:
    messages = _parse_conversation(test_case.get("conversation"))
    mock_context = _parse_json_field(test_case.get("mock_account_state"))
    # For multi-turn conversations, pass the full history; only the last message
    # is the one the agent needs to respond to.
    agent_resp = _call_agent_full(messages, mock_context)
    if "error" in agent_resp:
        return agent_resp, {"verdict": "fail", "score": 0.0, "reasoning": agent_resp["error"]}
    judgment = await judge_context_retention(test_case, agent_resp, calibrate)
    return agent_resp, judgment


# Map sheet name → runner function
_SHEET_RUNNERS = {
    "Input Guard":          run_input_guard,
    "Intent Classifier":    run_intent_classifier,
    "Output Guard":         run_output_guard,
    "KB Retrieval":         run_kb_retrieval,
    "Action Execution":     run_action_execution,
    "Escalation":           run_escalation,
    "Conversation Quality": run_conversation_quality,
    "PII & Data Leakage":   run_pii_leakage,
    "Policy Compliance":    run_policy_compliance,
    "Graceful Failure":     run_graceful_failure,
    "Context Retention":    run_context_retention,
}


# ---------------------------------------------------------------------------
# Results writing
# ---------------------------------------------------------------------------

def _append_run_column(ws, tag: str, row_results: list, sheet_cost: float):
    """
    Append 3 columns to a test sheet for this run:
      "{tag} ($X.XXX)"  — PASS / PARTIAL / FAIL  (color-coded; cost in header)
      "{tag} response"  — agent's response text (truncated to 500 chars)
      "{tag} reasoning" — judge's reasoning

    row_results is a list aligned to data rows (row 2+).
    """
    col = ws.max_column + 1
    fill_map  = {"pass": FILL_PASS, "partial": FILL_PARTIAL, "fail": FILL_FAIL}
    label_map = {"pass": "PASS", "partial": "PARTIAL", "fail": "FAIL"}

    verdict_header = f"{tag} (${sheet_cost:.3f})"
    for offset, title in enumerate([verdict_header, f"{tag} response", f"{tag} reasoning"]):
        cell = ws.cell(1, col + offset, title)
        cell.fill = FILL_HEADER
        cell.font = FONT_BOLD

    for i, case in enumerate(row_results):
        result     = case.get("result", {})
        agent_resp = case.get("agent_response", {})
        verdict    = result.get("verdict", "fail")
        row        = i + 2

        verdict_cell = ws.cell(row, col, label_map.get(verdict, "FAIL"))
        verdict_cell.fill = fill_map.get(verdict, FILL_FAIL)

        response_text = str(agent_resp.get("response", "") or "")
        ws.cell(row, col + 1, response_text[:500])

        ws.cell(row, col + 2, result.get("reasoning", ""))


def _ensure_run_history_sheet(wb) -> openpyxl.worksheet.worksheet.Worksheet:
    """Create or return the Run History sheet."""
    if RUN_HISTORY_SHEET in wb.sheetnames:
        return wb[RUN_HISTORY_SHEET]
    ws = wb.create_sheet(RUN_HISTORY_SHEET)
    headers = ["run_id", "date", "version_tag", "change_description",
               "eval_type", "pass%", "total_tokens", "total_cost_usd", "judge_model", "notes"]
    for c, h in enumerate(headers, 1):
        cell = ws.cell(1, c, h)
        cell.fill = FILL_HEADER
        cell.font = FONT_BOLD
    return ws


def _append_run_history(ws, run_id: int, tag: str, desc: str,
                         sheet_pass_rates: dict, sheet_costs: dict,
                         sheet_tokens: dict, calibrate: bool):
    """
    Append one row per evaluated sheet plus an OVERALL row.
    Columns: run_id | date | version_tag | change_description | eval_type | pass% |
             total_tokens | total_cost_usd | judge_model | notes
    """
    judge_model = (
        JUDGE_MODEL_CALIBRATION if calibrate
        else f"classification: {JUDGE_MODEL_CLASSIFICATION} | behavioral+safety: {JUDGE_MODEL_BEHAVIORAL}"
    )
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    for sheet, rate in sheet_pass_rates.items():
        tokens = sheet_tokens.get(sheet, 0)
        cost   = sheet_costs.get(sheet, 0.0)
        ws.append([run_id, date_str, tag, desc, sheet,
                   round(rate * 100, 1), tokens, round(cost, 4), judge_model, ""])

    overall_scores = list(sheet_pass_rates.values())
    overall_pass   = sum(overall_scores) / len(overall_scores) if overall_scores else 0.0
    overall_tokens = sum(sheet_tokens.values())
    overall_cost   = sum(sheet_costs.values())
    ws.append([run_id, date_str, tag, desc, "OVERALL",
               round(overall_pass * 100, 1), overall_tokens,
               round(overall_cost, 4), judge_model, ""])
    last_row = ws.max_row
    for c in range(1, 11):
        ws.cell(last_row, c).font = FONT_BOLD




# ---------------------------------------------------------------------------
# Pre-run cost estimation
# ---------------------------------------------------------------------------

def _estimate_cost_per_call(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost for a call using MODEL_PRICE_PER_TOKEN table."""
    prices = MODEL_PRICE_PER_TOKEN.get(model)
    if not prices:
        return 0.0
    return prompt_tokens * prices["input"] + completion_tokens * prices["output"]


def _estimate_run_cost(wb, target_sheets: list, calibrate: bool) -> tuple:
    """
    Estimate total cost before running.
    Returns (total_cost, breakdown) where breakdown is a list of
    (sheet_name, n_cases, agent_cost, judge_cost, sheet_total) tuples.
    """
    breakdown = []
    total = 0.0

    for sheet_name in target_sheets:
        if sheet_name not in wb.sheetnames or sheet_name not in SHEET_CALL_PROFILE:
            continue
        n_cases = wb[sheet_name].max_row - 1
        profile = SHEET_CALL_PROFILE[sheet_name]

        agent_cost = 0.0
        if profile["agent"]:
            per_call = _estimate_cost_per_call(
                AGENT_MODEL,
                AVG_AGENT_PROMPT_TOKENS,
                AVG_AGENT_COMPLETION_TOKENS,
            )
            agent_cost = n_cases * per_call

        judge_cost = 0.0
        if profile["judge"] != "none" and profile["judge_rate"] > 0:
            judge_model = (
                JUDGE_MODEL_CALIBRATION if calibrate
                else JUDGE_MODEL_CLASSIFICATION if profile["judge"] == "classification"
                else JUDGE_MODEL_BEHAVIORAL
            )
            per_judge = _estimate_cost_per_call(
                judge_model,
                AVG_JUDGE_PROMPT_TOKENS,
                AVG_JUDGE_COMPLETION_TOKENS,
            )
            judge_cost = n_cases * profile["judge_rate"] * per_judge

        sheet_total = agent_cost + judge_cost
        total += sheet_total
        breakdown.append((sheet_name, n_cases, agent_cost, judge_cost, sheet_total))

    return total, breakdown


def _print_cost_estimate(breakdown: list, total: float) -> bool:
    """Print the pre-run cost estimate and prompt for confirmation. Returns True to proceed."""
    print("\n=== Pre-run cost estimate ===")
    print(f"  {'Sheet':<25} {'Cases':>5}  {'Agent':>8}  {'Judge':>8}  {'Total':>8}")
    print(f"  {'-'*25} {'-'*5}  {'-'*8}  {'-'*8}  {'-'*8}")
    for sheet_name, n_cases, agent_cost, judge_cost, sheet_total in breakdown:
        print(f"  {sheet_name:<25} {n_cases:>5}  ${agent_cost:>7.3f}  ${judge_cost:>7.3f}  ${sheet_total:>7.3f}")
    print(f"  {'-'*25} {'-'*5}  {'-'*8}  {'-'*8}  {'-'*8}")
    print(f"  {'TOTAL':<25} {'':>5}  {'':>8}  {'':>8}  ${total:>7.3f}")
    print()
    print("Note: agent costs are estimates (chars÷4 heuristic); judge costs use LiteLLM pricing.")
    answer = input("\nProceed? [y/N] ").strip().lower()
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def run_evals(tag: str, desc: str, sheets_filter: list, calibrate: bool):
    # 1. Load workbook
    if not os.path.exists(TEST_CASES_FILE):
        sys.exit(f"Test cases file not found: {TEST_CASES_FILE}")

    wb = openpyxl.load_workbook(TEST_CASES_FILE)

    # Determine which sheets to run
    target_sheets = sheets_filter if sheets_filter else SHEET_NAMES
    missing = [s for s in target_sheets if s not in wb.sheetnames]
    if missing:
        sys.exit(f"Sheets not found in workbook: {missing}")

    # 2. Verify agent is reachable
    print(f"Checking agent at {AGENT_TEST_ENDPOINT} ...")
    try:
        resp = requests.get(
            AGENT_TEST_ENDPOINT.replace("/api/chat/test", "/health"),
            timeout=5,
        )
        if resp.status_code != 200:
            sys.exit(f"Agent health check failed: {resp.status_code}")
        print("Agent is up.\n")
    except requests.exceptions.ConnectionError:
        sys.exit(
            f"Cannot reach agent at {AGENT_TEST_ENDPOINT.replace('/api/chat/test', '')}\n"
            "Make sure it is running with APP_ENV=test."
        )

    # 3. Pre-run cost estimate + confirmation
    estimated_total, cost_breakdown = _estimate_run_cost(wb, target_sheets, calibrate)
    if not _print_cost_estimate(cost_breakdown, estimated_total):
        print("Aborted.")
        return

    # 4. Run each sheet
    all_results: dict[str, list] = {}
    sheet_pass_rates: dict[str, float] = {}
    sheet_costs: dict[str, float] = {}    # actual costs per sheet
    sheet_tokens: dict[str, int] = {}     # actual tokens per sheet

    for sheet_name in target_sheets:
        if sheet_name not in _SHEET_RUNNERS:
            print(f"[SKIP] {sheet_name} — no runner defined.")
            continue

        runner = _SHEET_RUNNERS[sheet_name]
        ws = wb[sheet_name]
        total_rows = ws.max_row - 1
        print(f"[{sheet_name}] Running {total_rows} cases...")

        case_results = []
        scores = []
        sheet_agent_tokens = 0
        sheet_agent_cost   = 0.0
        sheet_judge_cost   = 0.0

        for row_idx in range(2, ws.max_row + 1):
            test_case = _row_to_dict(ws, row_idx)
            test_id = test_case.get("test_id", f"row-{row_idx}")

            t0 = time.time()
            agent_resp, judgment = await runner(test_case, calibrate)
            latency = time.time() - t0

            # Accumulate agent token estimates
            p_tok = agent_resp.get("prompt_tokens", 0)
            c_tok = agent_resp.get("completion_tokens", 0)
            sheet_agent_tokens += p_tok + c_tok
            if p_tok + c_tok > 0:
                sheet_agent_cost += _estimate_cost_per_call(
                    AGENT_MODEL, p_tok, c_tok
                )

            # Accumulate judge cost (exact, from LiteLLM)
            sheet_judge_cost += judgment.get("cost_usd", 0.0)

            verdict = judgment.get("verdict", "fail")
            score   = judgment.get("score", 0.0)
            scores.append(score)

            case_cost = judgment.get("cost_usd", 0.0) + _estimate_cost_per_call(
                AGENT_MODEL, p_tok, c_tok
            )
            verdict_label = {"pass": "PASS", "partial": "PART", "fail": "FAIL"}.get(verdict, "FAIL")
            print(f"  {test_id}: {verdict_label}  ({latency:.1f}s)  ${case_cost:.4f}  {judgment.get('reasoning', '')[:70]}")

            case_results.append({
                "test_id": test_id,
                "result": judgment,
                "agent_response": agent_resp,
                "latency_s": latency,
            })

        pass_rate   = sum(scores) / len(scores) if scores else 0.0
        sheet_total_cost = sheet_agent_cost + sheet_judge_cost
        sheet_pass_rates[sheet_name] = pass_rate
        sheet_costs[sheet_name]      = sheet_total_cost
        sheet_tokens[sheet_name]     = sheet_agent_tokens
        all_results[sheet_name]      = case_results
        print(f"  → Pass rate: {pass_rate * 100:.1f}%  |  agent: ${sheet_agent_cost:.4f}  judge: ${sheet_judge_cost:.4f}  total: ${sheet_total_cost:.4f}\n")

        _append_run_column(ws, tag, case_results, sheet_total_cost)

    # 5. Append to Run History sheet
    rh_ws  = _ensure_run_history_sheet(wb)
    run_id = rh_ws.max_row
    _append_run_history(rh_ws, run_id, tag, desc, sheet_pass_rates, sheet_costs, sheet_tokens, calibrate)

    # 6. Save
    wb.save(TEST_CASES_FILE)
    print(f"Updated {TEST_CASES_FILE} with results.")

    # 7. Print summary
    print("\n=== Run Summary ===")
    print(f"Tag:  {tag}")
    if desc:
        print(f"Desc: {desc}")
    print()
    overall_scores = list(sheet_pass_rates.values())
    overall = sum(overall_scores) / len(overall_scores) if overall_scores else 0.0
    total_actual_cost = sum(sheet_costs.values())
    print(f"  {'Sheet':<25}  {'Pass%':>6}  {'Cost':>8}")
    print(f"  {'-'*25}  {'-'*6}  {'-'*8}")
    for sheet, rate in sheet_pass_rates.items():
        bar = "█" * int(rate * 20) + "░" * (20 - int(rate * 20))
        print(f"  {sheet:<25}  {rate * 100:>5.1f}%  ${sheet_costs.get(sheet, 0):.4f}")
    print(f"  {'-'*25}  {'-'*6}  {'-'*8}")
    print(f"  {'OVERALL':<25}  {overall * 100:>5.1f}%  ${total_actual_cost:.4f}")
    print(f"\n  Estimated: ${estimated_total:.4f}  |  Actual: ${total_actual_cost:.4f}")


def main():
    parser = argparse.ArgumentParser(
        description="Run evals against the AI support agent."
    )
    parser.add_argument(
        "--tag", required=True,
        help="Version tag for this run (e.g. v1.0, v1.1_intent_fix). Used as column header.",
    )
    parser.add_argument(
        "--desc", default="",
        help="Short description of what changed in this run.",
    )
    parser.add_argument(
        "--sheets", default="",
        help="Comma-separated list of sheets to run (default: all). "
             "E.g. 'Input Guard,Intent Classifier'",
    )
    parser.add_argument(
        "--calibrate", action="store_true",
        help="Use the calibration model (Opus) for all judgments instead of the standard tiered approach.",
    )
    args = parser.parse_args()

    sheets_filter = [s.strip() for s in args.sheets.split(",") if s.strip()] if args.sheets else []

    asyncio.run(run_evals(args.tag, args.desc, sheets_filter, args.calibrate))


if __name__ == "__main__":
    main()
