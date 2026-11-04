"""Per-turn LLM usage and cost, captured through a LiteLLM callback.

The stream endpoint opens a TurnUsage for the request and stores it in a
ContextVar; every LiteLLM call made while the graph runs (guards, classifier,
loop decision, response generator, embeddings) reports into it through the
callback, with no changes to the agent code. ContextVars propagate into the
tasks asyncio creates, which covers LangGraph's node tasks, the guard +
classifier gather, and LiteLLM's own logging task.

The callback fires asynchronously after each call returns, so a turn can
finish before its last call has been recorded — ``wait_settled`` covers that.
"""
import asyncio
import contextvars
import logging
from typing import Optional

import litellm
from litellm.integrations.custom_logger import CustomLogger

from backend.demo.pricing import estimate_cost

logger = logging.getLogger(__name__)

_current: "contextvars.ContextVar[Optional[TurnUsage]]" = contextvars.ContextVar(
    "demo_turn_usage", default=None
)

_COMPLETION_CALL_TYPES = {"completion", "acompletion", "text_completion", "atext_completion"}


class TurnUsage:
    def __init__(self):
        self.llm_calls = 0
        self.embedding_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0
        self.priced_calls = 0
        self.unpriced_calls = 0
        self.pending = 0
        self.calls: list = []  # [{model, call_type, prompt_tokens, completion_tokens, cost_usd}]

    @property
    def cost_basis(self) -> str:
        if self.llm_calls == 0 and self.embedding_calls == 0:
            return "unavailable"
        return "measured" if self.unpriced_calls == 0 else "partial"

    def record(self, model: str, call_type: str, prompt_tokens: int, completion_tokens: int,
               cost: "float | None") -> None:
        if cost is None:
            cost = estimate_cost(model, prompt_tokens, completion_tokens)
        is_completion = call_type in _COMPLETION_CALL_TYPES
        if is_completion:
            self.llm_calls += 1
        else:
            self.embedding_calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        if cost is None:
            self.unpriced_calls += 1
        else:
            self.priced_calls += 1
            self.cost_usd += float(cost)
        self.calls.append({
            "model": model, "call_type": call_type,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "cost_usd": cost,
        })

    async def wait_settled(self, timeout: float = 2.0) -> bool:
        """Wait for in-flight callbacks to report. Returns False on timeout."""
        deadline = asyncio.get_running_loop().time() + timeout
        while self.pending > 0:
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.02)
        return True

    def as_dict(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "embedding_calls": self.embedding_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6) if self.cost_basis != "unavailable" else None,
            "cost_basis": self.cost_basis,
        }


def start(usage: TurnUsage) -> contextvars.Token:
    return _current.set(usage)


def stop(token: contextvars.Token) -> None:
    _current.reset(token)


def current() -> Optional[TurnUsage]:
    return _current.get()


def _usage_from_response(response_obj) -> tuple:
    usage = getattr(response_obj, "usage", None)
    if usage is None and isinstance(response_obj, dict):
        usage = response_obj.get("usage")
    if usage is None:
        return 0, 0
    get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
    return int(get("prompt_tokens") or 0), int(get("completion_tokens") or 0)


class TurnUsageLogger(CustomLogger):
    """LiteLLM callback that reports into the current TurnUsage (if any)."""

    def log_pre_api_call(self, model, messages, kwargs):
        u = _current.get()
        if u is not None:
            u.pending += 1

    def _settle(self):
        u = _current.get()
        if u is not None and u.pending > 0:
            u.pending -= 1
        return u

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        u = self._settle()
        if u is None:
            return
        try:
            prompt_tokens, completion_tokens = _usage_from_response(response_obj)
            cost = kwargs.get("response_cost")
            u.record(
                model=str(kwargs.get("model") or ""),
                call_type=str(kwargs.get("call_type") or "acompletion"),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=float(cost) if cost else None,
            )
        except Exception:  # never let telemetry break a turn
            logger.exception("TurnUsageLogger failed to record a call")

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._settle()

    # Sync variants — the backend only makes async calls, but keep the counter honest.
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._settle()

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._settle()


def install() -> None:
    """Register the callback once per process (idempotent)."""
    if not any(isinstance(cb, TurnUsageLogger) for cb in litellm.callbacks):
        litellm.callbacks.append(TurnUsageLogger())
