All eligibility validation logic lives in the shared sync helpers in `order_tools.py` — never directly in individual tools.

- `_check_cancel_eligibility_sync(order)` — status checks for cancellation
- `_check_refund_eligibility_sync(order, products, reason, now)` — status checks, final sale flags, non-returnable categories, return windows, defective escalation

Both the read-only eligibility tool (`check_cancel_eligibility`, `check_refund_eligibility`) and the write tool (`cancel_order`, `process_refund`) call the same sync helper. A new business rule added to the helper is automatically enforced in both paths.

**When adding a new eligibility rule** (e.g. a new non-returnable category, a new status restriction, a change to the return window logic): put it in the sync helper only. Do not add parallel checks in the write tool.
