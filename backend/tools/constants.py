"""
Shared constants for tool parameter validation.
"""

# Valid values for the reason parameter across cancel_order, process_refund, and initiate_return.
# Single source of truth — the intent prompt and tool validation both reference this list.
REASON_VALUES: list = [
    "changed_mind",
    "wrong_item",
    "wrong_size",
    "defective",
    "not_as_described",
    "found_cheaper",
    "late_delivery",
    "other",
]
