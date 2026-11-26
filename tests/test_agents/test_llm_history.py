"""History sent to the LLM never starts with an agent turn (the API rejects it)."""
from backend.agents.conversation import CHAT_OPENED_PLACEHOLDER, _llm_history
from backend.routers.chat import greeting_for


def _convo(n_pairs: int) -> list:
    msgs = []
    for i in range(n_pairs):
        msgs += [{"role": "customer", "content": f"q{i}"}, {"role": "agent", "content": f"a{i}"}]
    return msgs + [{"role": "customer", "content": "last"}]


def test_long_conversation_window_starts_at_a_customer_message():
    # 11 messages: the last 10 used to start on an agent reply (classifier failure)
    h = _llm_history(_convo(5), 10)
    assert h[0]["role"] == "user" and h[0]["content"] != CHAT_OPENED_PLACEHOLDER
    assert h[-1] == {"role": "user", "content": "last"}
    assert len(h) == 9


def test_saved_greeting_gets_the_placeholder_in_front():
    msgs = [{"role": "agent", "content": greeting_for("Arwen")}, {"role": "customer", "content": "hi"}]
    h = _llm_history(msgs, 10)
    assert h == [
        {"role": "user", "content": CHAT_OPENED_PLACEHOLDER},
        {"role": "assistant", "content": "Hi Arwen, I'm an AI assistant. What can I help with?"},
        {"role": "user", "content": "hi"},
    ]


def test_ordinary_history_is_unchanged():
    h = _llm_history(_convo(2), 50)
    assert [m["content"] for m in h] == ["q0", "a0", "q1", "a1", "last"]
