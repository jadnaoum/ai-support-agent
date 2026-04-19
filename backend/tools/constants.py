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
    "duplicate_item",
    "not_as_described",
    "found_cheaper",
    "late_delivery",
    "other",
]

# Reasons that require human review regardless of which tool is invoked.
# Any tool that accepts a reason parameter must reject these at entry before executing.
ESCALATION_REASONS: frozenset = frozenset({"defective", "duplicate_item"})

# Canonical set of output reason codes returned by tools.
# Distinct from REASON_VALUES (which are input reasons from the classifier).
OUTPUT_REASONS: frozenset = frozenset({
    # Cancellation eligibility
    "eligible",
    "already_shipped",
    "already_delivered",
    "already_cancelled",
    "already_returned",
    "already_refunded",
    # Return eligibility
    "defective",
    "duplicate_item",
    "outside_return_window",
    "final_sale",
    "non_returnable_category",
    "wrong_status",
    "already_in_progress",
    # Write tool input validation
    "order_id_required",
    "reason_required",
    "invalid_reason",
    "confirmation_required",
    # Action success
    "cancelled",
    "return_initiated",
    "return_pending_review",
    # Informational
    "tracking_info",
    "refund_list",
    "no_refunds",
    # Missing package
    "wait_for_delivery",
    "carrier_claim_eligible",
})
