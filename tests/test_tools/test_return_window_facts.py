"""
Return eligibility results carry the window facts (UI test conv 5): the agent
answers "until when?" from return_deadline / return_window_starts instead of
computing a date itself. Same shared helper for real tools and mocks.
"""
from datetime import datetime, timedelta, timezone

from backend.agents.mock_tools import mock_tool_call
from backend.tools.order_tools import _check_return_eligibility_data

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
LAMP = [{"name": "Desk lamp", "category": "home_goods", "return_window_days": 30, "final_sale": False}]


def test_not_yet_delivered_says_window_starts_on_delivery():
    r = _check_return_eligibility_data({"status": "shipped", "delivered_at": None}, LAMP, now=NOW)
    assert r["reason"] == "wrong_status"
    assert r["return_window_days"] == 30
    assert r["return_window_starts"] == "on_delivery"
    assert "return_deadline" not in r


def test_eligible_order_carries_deadline_from_delivery():
    delivered = NOW - timedelta(days=10)
    r = _check_return_eligibility_data({"status": "delivered", "delivered_at": delivered}, LAMP, now=NOW)
    assert r["eligible"] is True
    assert r["return_deadline"] == (delivered + timedelta(days=30)).date().isoformat()


def test_outside_window_carries_the_passed_deadline():
    delivered = NOW - timedelta(days=40)
    r = _check_return_eligibility_data({"status": "delivered", "delivered_at": delivered}, LAMP, now=NOW)
    assert r["reason"] == "outside_return_window"
    assert r["return_deadline"] == (delivered + timedelta(days=30)).date().isoformat()


def test_terminal_statuses_get_no_window_facts():
    r = _check_return_eligibility_data({"status": "cancelled", "delivered_at": None}, LAMP, now=NOW)
    assert r["reason"] == "wrong_status"
    assert "return_window_starts" not in r


def test_mock_layer_returns_the_same_facts():
    mock = {"orders": [{"id": "lamp-1", "status": "shipped", "total": 59.99, "placed_at": "2026-11-21",
                        "items": [{"name": "Desk lamp", "category": "home_goods"}]}]}
    r = mock_tool_call("check_return_eligibility", {"order_id": "lamp-1"}, mock, "c", [])
    assert r["return_window_starts"] == "on_delivery" and r["return_window_days"] == 30
