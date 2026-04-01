#!/usr/bin/env python3
"""
Probe the intent classifier in isolation for specific messages.
Prints the raw LLM response before and after JSON parsing.

Usage:
    python scripts/probe_intent.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm
from prompts.loader import get_prompt
from backend.config import get_settings

INTENT_PROMPT = get_prompt("intent_prompt")
settings = get_settings()

CASES = [
    {
        "id": "IC-012",
        "note": "Multi-turn: last message after KB question → pivots to cancel",
        "message": "OK then cancel my order #11122 instead",
    },
    {
        "id": "IC-021",
        "note": "Multi-turn: multiple complaints listed in one message",
        "message": "Order #99999. The color is wrong, it's the wrong size, and it arrived late.",
    },
    {
        "id": "IC-023",
        "note": "Two intents: shipping question first, cancel second",
        "message": "How long does shipping take and also can you cancel order #33333?",
    },
    {
        "id": "IC-027",
        "note": "Return with wrong item + scratch — wrong_item should dominate",
        "message": "I need to return this, I ordered the red one but got blue, and it has a scratch on it too",
    },
]


async def probe(case: dict):
    print(f"\n{'='*70}")
    print(f"  {case['id']}  |  {case['note']}")
    print(f"  Message: {case['message']}")
    print(f"{'='*70}")

    result = await litellm.acompletion(
        model=settings.litellm_model,
        messages=[
            {"role": "system", "content": INTENT_PROMPT},
            {"role": "user", "content": case["message"]},
        ],
        stream=False,
    )
    raw = result.choices[0].message.content.strip()

    print(f"\n--- Raw LLM output ---\n{raw}")

    # Mirror the same parsing logic as _classify_intent
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        print(f"\n--- Parsed ---")
        print(json.dumps(parsed, indent=2))
        intent = parsed.get("intent", "general")
        confidence = parsed.get("confidence")
        print(f"\n  intent:     {intent}")
        print(f"  confidence: {confidence}")
        if intent == "action_request":
            print(f"  action:     {parsed.get('action')}")
            print(f"  params:     {parsed.get('params')}")
        if intent == "needs_clarification":
            print(f"  clarification_prompt: {parsed.get('clarification_prompt')}")
    except json.JSONDecodeError as e:
        print(f"\n  [JSON parse error: {e}]")


async def main():
    for case in CASES:
        await probe(case)
    print(f"\n{'='*70}\nDone.\n")


if __name__ == "__main__":
    asyncio.run(main())
