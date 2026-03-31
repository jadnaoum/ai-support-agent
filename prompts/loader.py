"""
Prompt loader — reads production.yaml and eval_rubrics.yaml once at import time
and exposes all prompts by snake_case key.

Usage:
    from prompts.loader import get_prompt
    text = get_prompt("intent_prompt")
    filled = get_prompt("response_prompt").format(context_section=ctx)
"""
from pathlib import Path

import yaml

from backend.tools.constants import REASON_VALUES

_PROMPTS_DIR = Path(__file__).parent
_cache: dict = {}


def _load() -> dict:
    if not _cache:
        for fname in ("production.yaml", "eval_rubrics.yaml"):
            with open(_PROMPTS_DIR / fname, encoding="utf-8") as fh:
                _cache.update(yaml.safe_load(fh))
        # Resolve {reason_enum} in intent_prompt at load time so the YAML stays DRY.
        # Use str.replace (not .format) to avoid touching the JSON examples in the prompt.
        reason_enum_str = ", ".join(REASON_VALUES)
        if "intent_prompt" in _cache:
            _cache["intent_prompt"] = _cache["intent_prompt"].replace(
                "{reason_enum}", reason_enum_str
            )
    return _cache


def get_prompt(key: str) -> str:
    """Return the prompt string for *key*, raising KeyError if not found."""
    prompts = _load()
    if key not in prompts:
        raise KeyError(f"Prompt key '{key}' not found in prompts YAML files")
    return prompts[key]
