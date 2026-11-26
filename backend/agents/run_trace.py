"""
Per-request trace of things the final agent state doesn't show: fallbacks that
fired (a reply that came from an error path, not from the agent) and the
output guard's verdicts with its own explanation.

Recording is a no-op unless a collector is active. Only the eval endpoint
(/api/chat/test) opens one, so this never changes what the agent does — it
exists so evals can't mistake a silent fallback for real behaviour (the
redirect bug passed the Input Guard sheet for months because every redirect
fell back to the same generic sentence and the judge only checked the label).
"""
import contextvars
from typing import List, Optional

_current: "contextvars.ContextVar[Optional[List[dict]]]" = contextvars.ContextVar(
    "run_trace", default=None
)


def start() -> contextvars.Token:
    return _current.set([])


def stop(token: contextvars.Token) -> List[dict]:
    events = _current.get() or []
    _current.reset(token)
    return events


def record(kind: str, **payload) -> None:
    events = _current.get()
    if events is None:
        return
    try:
        events.append({"kind": kind, **payload})
    except Exception:  # tracing must never break a turn
        pass


def fallback(name: str, detail: str = "") -> None:
    """A fallback path produced (part of) this turn's behaviour."""
    record("fallback", name=name, detail=detail)
