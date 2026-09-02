"""Fallback per-token prices (USD) for turn cost when LiteLLM cannot price a
model itself (its table does not resolve short model IDs such as
``claude-sonnet-4-6`` reliably — see evals/config.py, which keeps the same
table for pre-run estimates; unify the two when evals are next touched)."""

MODEL_PRICE_PER_TOKEN: dict = {
    "claude-haiku-4-5-20251001": {"input": 1e-06,  "output": 5e-06},
    "claude-sonnet-4-6":         {"input": 3e-06,  "output": 1.5e-05},
    "claude-opus-4-6":           {"input": 1.5e-05, "output": 7.5e-05},
    "text-embedding-3-small":    {"input": 2e-08,  "output": 0.0},
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> "float | None":
    key = (model or "").split("/")[-1]
    prices = MODEL_PRICE_PER_TOKEN.get(key)
    if not prices:
        return None
    return prompt_tokens * prices["input"] + completion_tokens * prices["output"]
