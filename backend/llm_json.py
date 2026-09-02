"""
Shared JSON extraction for LLM outputs.

All three JSON-over-prose call sites (intent classifier, loop decision, input
guard) parse the same way through extract_json_object() so recovery behavior
cannot drift between them. Recovery layers, in order:
  1. Strip markdown code fences if the LLM wraps the JSON.
  2. Slice from the first `{` to the last `}` — handles reasoning preambles.
  3. On parse failure, depth-count from the first `{` to its matching `}` and
     parse only the first complete object — handles model self-correction
     emitting a second JSON block (see ARCHITECTURE.md "Intent classifier
     output handling").

Raises ValueError when no JSON object can be recovered. Callers own their
fallback behavior (fail-open vs fail-closed vs default intent) and must log it.
"""
import json
import logging

logger = logging.getLogger(__name__)


def extract_json_object(raw: str, caller: str = "") -> dict:
    """Extract and parse the first JSON object from an LLM completion.

    Args:
        raw: the LLM completion text.
        caller: label for log lines (e.g. "_classify_intent").

    Returns:
        The parsed dict.

    Raises:
        ValueError: no JSON object could be recovered from the text.
    """
    text = (raw or "").strip()

    # Layer 1: strip markdown code fences
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    # Layer 2: slice from first `{` to last `}` — drops reasoning preambles/suffixes
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]

    try:
        parsed = json.loads(text.strip())
        if not isinstance(parsed, dict):
            raise ValueError(f"{caller}: parsed JSON is not an object")
        return parsed
    except json.JSONDecodeError:
        pass

    # Layer 3: multiple JSON blocks (model self-correction) — depth-count from
    # the first `{` to find its matching `}` and use only the first object.
    first_start = text.find("{")
    if first_start == -1:
        raise ValueError(f"{caller}: no JSON object found in LLM output")
    depth = 0
    first_end = -1
    for i, ch in enumerate(text[first_start:], start=first_start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                first_end = i
                break
    if first_end == -1:
        raise ValueError(f"{caller}: unterminated JSON object in LLM output")
    first_block = text[first_start:first_end + 1]
    try:
        parsed = json.loads(first_block)
    except json.JSONDecodeError as e:
        raise ValueError(f"{caller}: first JSON block failed to parse") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"{caller}: parsed JSON is not an object")
    logger.info("%s recovered first JSON block after parse failure", caller or "extract_json_object")
    return parsed
