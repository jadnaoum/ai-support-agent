# Eval Conventions

## mock_account_state item categories

Every item in `mock_account_state.orders[].items` must include a `category` field.

**Valid values:** `"electronics"`, `"clothing"`, `"accessories"`, `"home_goods"`

This field drives per-category return windows in mock tools:
- `"electronics"` → 14-day return window
- Everything else → 30-day return window

When adding new test cases with mock orders, always include `category` on every item.

**Inference guide:**
- Headphones, speakers, laptops, phones, tablets, chargers, USB hubs → `"electronics"`
- Jackets, shirts, shoes, clothing, apparel → `"clothing"`
- Phone cases, bags, sleeves, screen protectors → `"accessories"`
- Coffee makers, blenders, lamps, air purifiers, kitchen scales → `"home_goods"`

## Warranty, final_sale, and legacy fixtures

Mock eligibility runs the SAME shared helpers as production (`_check_*_eligibility_data` in `order_tools.py`). Fixture semantics:

- `warranty_months` on an item: months of warranty coverage. **Absent or null = no warranty** (matches production — a defective claim outside the return window with no warranty dead-ends instead of escalating). Set it explicitly whenever a case depends on warranty coverage.
- `final_sale: true` on an item: makes the order ineligible for returns.
- `return_window_days` on an item: explicit override; otherwise derived from `category` (electronics 14, else 30).
- **Legacy fixtures only:** an order with no `items` at all (or the old `"item": "<name>"` string form) gets one synthetic generic item — 30-day window, 12-month warranty — reproducing the old mock defaults. Never rely on this for new cases; always specify `items` with `category`.

## Aspiration tiers (`aspiration` column)

Every test sheet has an `aspiration` column (last column, after the run columns):

- **empty / `current`** (default): the system should pass this today. Failures are regressions or bugs. Feeds the regression pass rate.
- **`target`**: encodes the IDEAL customer outcome the system cannot reach yet. Runs and is judged normally, but is EXCLUDED from the regression pass rate; reported separately as "target coverage: passed/total" (runner output + Run History notes). Sheet cells show TARGET PASS / TARGET FAIL.

Rules for target cases:
- Every target case must be paired with a PENDING_CHANGES item describing the capability it waits on. When the capability ships, flip the case to `current`.
- Target rubrics must demand **structural evidence** (a tool result, a state flag), never just response text — otherwise a hallucinating agent "reaches" the ideal by fabricating it, and tuning against the case trains dishonesty.
- The ideal is conditioned on truth: given the world the fixture describes, what would the ideal agent do? A pass path that requires the agent to claim data or actions it doesn't have is invalid in any tier.

## Injected KB context: use kb_ref, never pasted text

When a fixture pre-loads KB content via `mock_agent_state.retrieved_context`, reference the KB instead of pasting text:

```json
{"retrieved_context": [{"kb_ref": {"file": "returns_and_refunds.md", "heading": "Condition Requirements for Returns"}, "similarity": 0.85}]}
```

The runner resolves refs to live `docs/kb/` text at run time (`_resolve_kb_refs` in run_evals.py) — chunk_text, title, and category are filled from the file. A missing file/heading fails the run loudly. Pasted chunk_text is forbidden in new cases: it drifts silently when the KB changes (this rotted for months before the 2026-09 audit).
