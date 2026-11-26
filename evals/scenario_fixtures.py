"""
Demo scenarios as eval fixtures.

A case's mock_account_state can reference a demo scenario instead of a
hand-written fixture:

    {"scenario": "broken-after-months", "name": "Geralt"}

The runner expands it (evals/run_evals.py `_parse_json_field`) into the same
orders a demo visitor gets — products from backend/db/seed.py
PRODUCT_CATALOG, statuses and ages from backend/demo/scenarios.py, delivery
2 days after ordering like backend/demo/customers.order_dates — with
$EVAL_DATE placeholders so the ages stay correct forever. Any other keys in
the cell (e.g. "refunds", extra flags) are kept; "orders" in the cell, if
given, replaces the scenario's orders.

Order ids are deterministic per scenario + position (uuid5), so rubrics can
name them. scenario_order_id() returns them for case authors.
"""
import uuid

from backend.db.seed import PRODUCT_CATALOG
from backend.demo.scenarios import RELATIVE_DATES, scenario_by_id

_NS = uuid.UUID("6f1c2b6e-8a3f-4f4e-9c55-2c0f3f7d1a10")


def scenario_order_id(scenario_id: str, index: int) -> str:
    return str(uuid.uuid5(_NS, f"{scenario_id}/{index}"))


def scenario_fixture(scenario_id: str, name: str = "Test Customer") -> dict:
    scenario = scenario_by_id(scenario_id)
    if scenario is None:
        raise ValueError(f"Unknown demo scenario '{scenario_id}' in mock_account_state")
    orders, refunds = [], []
    for index, spec in enumerate(scenario["orders"]):
        product = PRODUCT_CATALOG[spec["product"]]
        days, _ = RELATIVE_DATES[spec["ordered_relative"]]
        item = {"name": product["name"], "qty": 1, "price": product["price"],
                "category": product["category"], "return_window_days": product["return_window_days"]}
        if product.get("warranty_months"):
            item["warranty_months"] = product["warranty_months"]
        if product.get("final_sale"):
            item["final_sale"] = True
        order = {"id": scenario_order_id(scenario_id, index), "status": spec["status"],
                 "total": product["price"], "placed_at": f"$EVAL_DATE-{days}d", "items": [item]}
        if spec["status"] in ("delivered", "returned"):
            order["delivered_date"] = f"$EVAL_DATE-{max(days - 2, 0)}d"
        if spec["status"] == "returned":
            refunds.append({"order_id": order["id"], "amount": product["price"], "status": "processed",
                            "reason": "changed_mind", "created_at": f"$EVAL_DATE-{max(days - 5, 0)}d"})
        orders.append(order)
    # Newest first, like get_customer_context — the mock layer treats fixture
    # order as recency (orders[0] = newest).
    orders.sort(key=lambda o: int(o["placed_at"].split("-")[-1].rstrip("d")))
    fixture = {"customer_id": "C-001", "name": name, "orders": orders}
    if refunds:
        fixture["refunds"] = refunds
    return fixture


def expand_scenario(state: dict) -> dict:
    """mock_account_state with a "scenario" key → full fixture (other keys kept)."""
    if not isinstance(state, dict) or "scenario" not in state:
        return state
    extra = {k: v for k, v in state.items() if k != "scenario"}
    fixture = scenario_fixture(state["scenario"], extra.pop("name", "Test Customer"))
    fixture.update(extra)
    return fixture
