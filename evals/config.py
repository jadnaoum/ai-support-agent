"""
Eval framework configuration.

All values are read from environment variables with sensible defaults.
Set these in your .env or export them before running evals.
"""
import os

# ---------------------------------------------------------------------------
# Agent under test
# ---------------------------------------------------------------------------

AGENT_BASE_URL = os.environ.get("EVAL_AGENT_URL", "http://localhost:8000")
AGENT_TEST_ENDPOINT = f"{AGENT_BASE_URL}/api/chat/test"

# The agent must be running with APP_ENV=test for the test endpoint to respond.
# Default customer_id used when test cases don't supply one.
DEFAULT_CUSTOMER_ID = "eval-test-customer"

# HTTP timeout for each agent call (seconds)
AGENT_TIMEOUT = 60

# ---------------------------------------------------------------------------
# Judge models (all calls go through LiteLLM)
# ---------------------------------------------------------------------------

# Classification judge — input guard, intent classifier (programmatic + light LLM)
JUDGE_MODEL_CLASSIFICATION = os.environ.get(
    "EVAL_JUDGE_CLASSIFICATION", "claude-haiku-4-5-20251001"
)

# Behavioral / safety judge — rubric-based scoring
JUDGE_MODEL_BEHAVIORAL = os.environ.get(
    "EVAL_JUDGE_BEHAVIORAL", "claude-sonnet-4-6"
)

# Calibration judge — used for --calibrate runs only (Opus)
JUDGE_MODEL_CALIBRATION = os.environ.get(
    "EVAL_JUDGE_CALIBRATION", "claude-opus-4-6"
)

# ---------------------------------------------------------------------------
# Pass / fail thresholds
# ---------------------------------------------------------------------------

# Minimum pass rate (0.0–1.0) per sheet before the overall run is considered degraded
PASS_RATE_THRESHOLD = float(os.environ.get("EVAL_PASS_RATE_THRESHOLD", "0.75"))

# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
TEST_CASES_FILE = os.path.join(EVALS_DIR, "eval_test_cases.xlsx")
RESULTS_DIR = os.path.join(EVALS_DIR, "results")
EVAL_RUNS_DIR = os.path.join(EVALS_DIR, "eval_runs")

# ---------------------------------------------------------------------------
# Cost estimation — average token counts per call type
# Used for pre-run estimates only; actual costs come from LiteLLM responses.
# ---------------------------------------------------------------------------

# Average tokens for a full agent test call (system prompt + messages + context + response)
AVG_AGENT_PROMPT_TOKENS    = 1500
AVG_AGENT_COMPLETION_TOKENS = 400

# Average tokens for an LLM judge call
AVG_JUDGE_PROMPT_TOKENS    = 800
AVG_JUDGE_COMPLETION_TOKENS = 100

# Per-sheet call profile:
#   agent  — whether the sheet makes a full agent LLM call per case
#   judge  — "none" | "classification" | "behavioral" | "safety"
#   judge_rate — fraction of cases that actually trigger an LLM judge call
#                (classification judge is mostly programmatic; OG only falls back ~30%)
SHEET_CALL_PROFILE = {
    "Input Guard":          {"agent": True,  "judge": "none",           "judge_rate": 0.0},
    "Intent Classifier":    {"agent": True,  "judge": "none",           "judge_rate": 0.0},
    "Output Guard":         {"agent": False, "judge": "classification",  "judge_rate": 0.3},
    "KB Retrieval":         {"agent": True,  "judge": "behavioral",      "judge_rate": 1.0},
    "Action Execution":     {"agent": True,  "judge": "behavioral",      "judge_rate": 1.0},
    "Escalation":           {"agent": True,  "judge": "behavioral",      "judge_rate": 1.0},
    "Conversation Quality": {"agent": True,  "judge": "behavioral",      "judge_rate": 1.0},
    "PII & Data Leakage":   {"agent": True,  "judge": "safety",          "judge_rate": 1.0},
    "Policy Compliance":    {"agent": True,  "judge": "safety",          "judge_rate": 1.0},
    "Graceful Failure":     {"agent": True,  "judge": "safety",          "judge_rate": 1.0},
    "Context Retention":    {"agent": True,  "judge": "safety",          "judge_rate": 1.0},
}

# Agent model (mirrors backend/config.py litellm_model default)
AGENT_MODEL = os.environ.get("LITELLM_MODEL", "claude-sonnet-4-6")

# Per-token prices (USD) for pre-run cost estimation.
# LiteLLM's completion_cost() doesn't resolve these short model IDs reliably,
# so we keep a small manual table. Update if pricing changes.
MODEL_PRICE_PER_TOKEN = {
    "claude-haiku-4-5-20251001": {"input": 1e-06,  "output": 5e-06},
    "claude-sonnet-4-6":         {"input": 3e-06,  "output": 1.5e-05},
    "claude-opus-4-6":           {"input": 1.5e-05, "output": 7.5e-05},
}

# ---------------------------------------------------------------------------
# Sheet names (must match the xlsx exactly)
# ---------------------------------------------------------------------------

SHEET_NAMES = [
    "Input Guard",
    "Intent Classifier",
    "Output Guard",
    "KB Retrieval",
    "Action Execution",
    "Escalation",
    "Conversation Quality",
    "PII & Data Leakage",
    "Policy Compliance",
    "Graceful Failure",
    "Context Retention",
]

RUN_HISTORY_SHEET = "Run History"

# Judge type per sheet
SHEET_JUDGE_TYPE = {
    "Input Guard":          "classification",
    "Intent Classifier":    "classification",
    "Output Guard":         "classification",
    "KB Retrieval":         "behavioral",
    "Action Execution":     "behavioral",
    "Escalation":           "behavioral",
    "Conversation Quality": "behavioral",
    "PII & Data Leakage":   "safety",
    "Policy Compliance":    "safety",
    "Graceful Failure":     "safety",
    "Context Retention":    "safety",
}
