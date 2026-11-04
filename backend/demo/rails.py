"""Capacity rails for the public demo.

Tier-1 API limits allow only a few agent turns per minute across ALL
visitors. The TurnGate caps how many graph runs execute at once and reports a
queue position so the UI can say "one person ahead of you" instead of hanging.
"""
import asyncio
import hashlib
from contextlib import asynccontextmanager
from typing import Optional


class TurnGate:
    """Global concurrency gate for agent turns.

    The underlying asyncio.Semaphore is created lazily per event loop:
    Python 3.9 binds a Semaphore to the loop that creates it, which breaks
    under pytest-asyncio's function-scoped loops (see .claude/rules/testing.md).
    """

    def __init__(self, max_concurrent: int):
        self.max_concurrent = max(1, int(max_concurrent))
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._sem: Optional[asyncio.Semaphore] = None
        self.active = 0
        self.waiting = 0

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._sem is None or self._loop is not loop:
            self._loop = loop
            self._sem = asyncio.Semaphore(self.max_concurrent)
            self.active = 0
            self.waiting = 0
        return self._sem

    def queue_position(self) -> int:
        """0 when a slot is free right now, else the position a new arrival
        would take in the queue (1 = next up)."""
        self._semaphore()
        if self.active < self.max_concurrent:
            return 0
        return self.waiting + 1

    @asynccontextmanager
    async def slot(self):
        sem = self._semaphore()
        self.waiting += 1
        try:
            await sem.acquire()
        finally:
            self.waiting -= 1
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
            sem.release()


def hash_ip(ip: Optional[str]) -> str:
    """Stable, non-reversible key for per-IP caps. Stored in customer metadata
    instead of the raw address."""
    return hashlib.sha256((ip or "unknown").encode("utf-8")).hexdigest()[:16]
