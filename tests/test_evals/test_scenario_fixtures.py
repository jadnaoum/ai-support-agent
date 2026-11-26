"""Demo scenarios expand into eval fixtures that match what a demo visitor gets."""
from backend.agents.mock_tools import mock_tool_call
from backend.db.seed import PRODUCT_CATALOG
from evals.run_evals import _parse_json_field
from evals.scenario_fixtures import expand_scenario, scenario_fixture, scenario_order_id


def test_orders_newest_first_with_catalog_products():
    fx = scenario_fixture("broken-after-months", "Geralt")
    assert [o["items"][0]["name"] for o in fx["orders"]] == [
        PRODUCT_CATALOG["desk_lamp"]["name"], PRODUCT_CATALOG["blender"]["name"]]
    assert fx["orders"][1]["id"] == scenario_order_id("broken-after-months", 0)  # blender is spec index 0
    assert fx["orders"][1]["delivered_date"] == "$EVAL_DATE-148d"
    assert "delivered_date" not in fx["orders"][0]                              # lamp only shipped


def test_final_sale_and_refunds_carry_over():
    assert scenario_fixture("final-sale-regret")["orders"][0]["items"][0]["final_sale"] is True
    fx = scenario_fixture("double-dip")
    assert fx["refunds"][0]["order_id"] == fx["orders"][0]["id"]


def test_cell_keys_are_kept_and_orders_can_be_overridden():
    out = expand_scenario({"scenario": "missed-window", "name": "Ann", "debug_classifier": True})
    assert out["name"] == "Ann" and out["debug_classifier"] is True and len(out["orders"]) == 1
    assert expand_scenario({"orders": []}) == {"orders": []}                     # no scenario → untouched


def test_runner_expands_and_mock_tools_use_it():
    mock = _parse_json_field('{"scenario": "missed-window"}')
    result = mock_tool_call("check_return_eligibility", {"order_id": mock["orders"][0]["id"]}, mock, "c", [])
    assert result["reason"] == "outside_return_window"                           # laptop: 14 days, delivered 19 days ago
