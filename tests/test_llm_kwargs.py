"""Models that think by default get thinking switched off; Sonnet 4.6 gets nothing extra."""
from backend.config import llm_kwargs


def test_sonnet_46_unchanged():
    assert llm_kwargs("claude-sonnet-4-6") == {}


def test_thinking_off_for_new_models():
    assert llm_kwargs("claude-sonnet-5-5") == {"thinking": {"type": "between_tools"}}
    assert llm_kwargs("anthropic/claude-haiku-5-5") == {"thinking": {"type": "disabled"}}
