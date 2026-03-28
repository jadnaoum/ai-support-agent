"""
Input guardrail — runs before the conversation agent processes a message.

Two-stage approach:
  1. Fast pattern check: catch obvious prompt injection substrings immediately,
     with no LLM call.
  2. LLM classification: for messages that pass the pattern check, ask the LLM
     to label the message as safe / prompt_injection / abusive / off_topic.

Returns a dict:
  {"safe": True}
  {"safe": False, "reason": str, "blocked_response": str}
"""
import json
import re
import uuid
import litellm

from backend.config import get_settings
from prompts.loader import get_prompt

settings = get_settings()

# ---------------------------------------------------------------------------
# Fast-path patterns — obvious injection attempts caught without an LLM call
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[str] = [
    r"ignore (all |your )?(previous |prior |above )?instructions",
    r"disregard (all |your )?(previous |prior |above )?instructions",
    r"you are now",
    r"act as (if you are|a )",
    r"new persona",
    r"jailbreak",
    r"do anything now",
    r"dan mode",
    r"developer mode",
    r"system prompt",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"\[INST\]",
    r"###\s*(system|instruction)",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def _fast_injection_check(message: str) -> bool:
    """Return True if the message matches a known injection pattern."""
    return any(p.search(message) for p in _COMPILED_PATTERNS)


# ---------------------------------------------------------------------------
# LLM-based classifier
# ---------------------------------------------------------------------------

# PROMPT — edit in prompts/production.yaml
INPUT_GUARD_PROMPT = get_prompt("input_guard_prompt")

_BLOCKED_RESPONSES: dict[str, str] = {
    "prompt_injection": (
        "I'm here to help with your orders and account questions. "
        "I'm not able to process that kind of request."
    ),
    "abusive": (
        "I'd like to help you today, but I need our conversation to remain respectful. "
        "Please let me know how I can assist with your order or account."
    ),
    "off_topic": (
        "I specialise in e-commerce support — orders, returns, shipping, and account questions. "
        "Is there something along those lines I can help you with?"
    ),
}


async def check_input(message: str) -> dict:
    """
    Check a customer message before passing it to the conversation agent.

    Returns:
        {"safe": True} if the message is safe to process.
        {"safe": False, "reason": str, "blocked_response": str} otherwise.
    """
    # Stage 1: fast pattern check
    if _fast_injection_check(message):
        return {
            "safe": False,
            "reason": "prompt_injection",
            "blocked_response": _BLOCKED_RESPONSES["prompt_injection"],
        }

    # Stage 2: LLM classification
    try:
        result = await litellm.acompletion(
            model=settings.litellm_model,
            messages=[
                {"role": "system", "content": INPUT_GUARD_PROMPT},
                {"role": "user", "content": message},
            ],
            stream=False,
        )
        raw = result.choices[0].message.content.strip()
        parsed = json.loads(raw)
        category = parsed.get("category", "safe")
    except Exception:
        # On any failure, fail open — let the message through
        return {"safe": True}

    if category == "safe":
        return {"safe": True}

    return {
        "safe": False,
        "reason": category,
        "blocked_response": _BLOCKED_RESPONSES.get(
            category,
            "I'm not able to help with that. Is there something else I can assist you with?",
        ),
    }


async def log_blocked_attempt(db, conversation_id: str, message: str, guard_result: dict) -> None:
    """Write an audit_logs entry for a blocked input guard attempt.

    Only called when the guard returns safe=False AND a real conversation_id is
    present (i.e. the live SSE path, not the test endpoint).

    Args:
        db: AsyncSession — the active DB session for the current request.
        conversation_id: UUID string of the conversation.
        message: the original customer message that was blocked.
        guard_result: the dict returned by check_input() (must have safe=False).
    """
    from backend.db.models import AuditLog  # local import avoids circular deps at module load

    db.add(AuditLog(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        agent_type="input_guard",
        action="input_guard_blocked",
        input_data={"message": message},
        output_data={
            "category": guard_result.get("reason", "unknown"),
            "blocked_response": guard_result.get("blocked_response", ""),
        },
    ))
