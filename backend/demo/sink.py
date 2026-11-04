"""Side channel for progressive pipeline events.

The stream endpoint opens a TurnSink for the request and stores it in a
ContextVar; the guards and service nodes call ``emit()`` as they finish, and
the endpoint drains the queue into SSE ``step`` events while the graph is
still running. ``emit()`` is a no-op when no sink is set (the eval endpoint,
unit tests, scripts), so it never changes what the agent does.
"""
import asyncio
import contextvars
from typing import Optional


class TurnSink:
    def __init__(self):
        self.queue: "asyncio.Queue[dict]" = asyncio.Queue()
        self.events: list = []

    def push(self, event: dict) -> None:
        self.events.append(event)
        self.queue.put_nowait(event)


_current: "contextvars.ContextVar[Optional[TurnSink]]" = contextvars.ContextVar(
    "demo_turn_sink", default=None
)


def emit(event_type: str, **payload) -> None:
    """Report a pipeline step to the current turn's sink, if any."""
    sink = _current.get()
    if sink is None:
        return
    try:
        sink.push({"type": event_type, **payload})
    except Exception:  # telemetry must never break a turn
        pass


def start(sink: TurnSink) -> contextvars.Token:
    return _current.set(sink)


def stop(token: contextvars.Token) -> None:
    _current.reset(token)


def current() -> Optional[TurnSink]:
    return _current.get()
