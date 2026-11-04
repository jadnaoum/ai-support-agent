import asyncio

import litellm
import pytest

from backend.demo import usage as turn_usage
from backend.demo.usage import TurnUsage, TurnUsageLogger, install


def test_record_prices_known_models_and_flags_unknown():
    u = TurnUsage()
    u.record("claude-sonnet-4-6", "acompletion", 1000, 100, cost=None)  # fallback table
    assert u.llm_calls == 1 and u.cost_basis == "measured"
    assert abs(u.cost_usd - (1000 * 3e-06 + 100 * 1.5e-05)) < 1e-12
    u.record("mystery-model", "acompletion", 10, 10, cost=None)
    assert u.unpriced_calls == 1 and u.cost_basis == "partial"
    u.record("text-embedding-3-small", "aembedding", 50, 0, cost=0.000001)
    assert u.embedding_calls == 1 and u.llm_calls == 2
    d = u.as_dict()
    assert d["prompt_tokens"] == 1060 and d["completion_tokens"] == 110


def test_install_is_idempotent():
    before = len(litellm.callbacks)
    install()
    install()
    loggers = [cb for cb in litellm.callbacks if isinstance(cb, TurnUsageLogger)]
    assert len(loggers) == 1
    assert len(litellm.callbacks) <= before + 1


async def test_callback_records_into_current_turn_only():
    install()
    u = TurnUsage()
    token = turn_usage.start(u)
    try:
        # mock_response goes through LiteLLM's real logging pipeline without any network call
        await litellm.acompletion(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
            mock_response="hello there",
        )
        assert await u.wait_settled(timeout=3.0)
    finally:
        turn_usage.stop(token)
    assert u.llm_calls == 1
    assert u.prompt_tokens > 0 and u.completion_tokens > 0
    assert u.cost_usd > 0 and u.cost_basis == "measured"
    assert u.pending == 0

    # Outside a turn nothing is recorded (and nothing raises).
    await litellm.acompletion(
        model="claude-sonnet-4-6", messages=[{"role": "user", "content": "hi"}], mock_response="x",
    )
    await asyncio.sleep(0.05)
    assert u.llm_calls == 1


async def test_context_propagates_into_child_tasks():
    install()
    u = TurnUsage()
    token = turn_usage.start(u)
    try:
        async def call():
            await litellm.acompletion(
                model="claude-sonnet-4-6", messages=[{"role": "user", "content": "hi"}], mock_response="y",
            )
        await asyncio.gather(call(), call())
        assert await u.wait_settled(timeout=3.0)
    finally:
        turn_usage.stop(token)
    assert u.llm_calls == 2
