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
