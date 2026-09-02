All eligibility validation logic lives in the shared pure-data helpers in `order_tools.py` — never directly in individual tools, and never re-implemented in the mock layer.

- `_check_cancel_eligibility_data(status)` — status checks for cancellation
- `_check_return_eligibility_data(order, products, reason, now)` — status checks, final sale flags, non-returnable categories, return windows, defective/duplicate escalation

Both operate on plain dicts. Two adapter layers feed them:
- **Real tools** (`check_cancel_eligibility`, `check_return_eligibility`, `cancel_order`, `initiate_return`) go through the ORM wrappers `_check_cancel_eligibility_sync(order)` / `_check_return_eligibility_sync(order, products, ...)` in `order_tools.py`.
- **Mock tools** (`backend/agents/mock_tools.py`) go through `_order_data()` / `_product_data()`, which only normalize eval fixtures into the shared dict shapes (see eval-conventions.md for fixture defaults). No eligibility logic may live in the mock layer.

**When adding a new eligibility rule** (e.g. a new non-returnable category, a new status restriction, a change to the return window logic): put it in the `*_data` helper only. It is then automatically enforced in both read and write tools AND exercised by evals through the mock layer. Do not add parallel checks in the write tools or the mocks.
