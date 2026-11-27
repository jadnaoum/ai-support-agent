# Recent orders in LLM context — who sees what

The customer's recent-orders summary (`customer_context["recent_orders"]`, rendered by `backend/agents/order_context.py`) goes to each LLM step at a deliberately different level. Do not "unify" these for consistency:

- **Response agent + output guard:** full list (id, status, placed date, total, products). The guard must see exactly what the agent saw, or correct order details get flagged `unsupported_claim`.
- **Intent classifier:** `identity_only=True` — id, date, products, NO status/total. With status visible it matched "return my order" to the only delivered order (an eligibility judgment; eligibility belongs to the tools), even when the prompt said not to.
- **Loop-decision step:** no list (`with_orders=False`). With it, it picked an order_id itself for the cancel check instead of letting the tool list eligible orders.

No-`order_id` resolution is structural: `_no_order_id_selection_data` in `order_tools.py` (shared by real and mock tools) — 1 order → use it; 2+ → `order_id_required` + `candidate_orders`, and `_service_needs_loop` ends the turn on `candidate_orders` so the customer chooses. Never reintroduce a "newest order" default.

## Earlier turns' results and today's date (2026-11-27, Jad)

Same who-sees-what split for two more context blocks:

- **Earlier-turn context** (`state["earlier_context"]`, saved in `turn_state` by `carry_earlier_context` in `chat.py`): the last 3 turns' tool results + top 2 KB passages per turn. **Reply step + output guard only** (`with_earlier=True`; the guard gets them in `results_from_earlier_turns` / `kb_content`). Without it Sonnet 5.5 retracted true facts from its own earlier replies ("disregard the 1 year I mentioned"). Never give it to the classifier or loop-decision step: stale results there would skip a fresh lookup.
- **Today's date** (`today_line()` in `order_context.py`, UTC — the tools' clock): reply step + output guard (top of `<customer_orders>`). Lets "delivered yesterday" be checked; without it 5.5 "corrected" true relative dates.
- **The output guard sees both sides of the conversation** (last 6 messages, role-labelled), so a draft that repeats or corrects an earlier reply can be verified.

## Accepted risk: business logic derived from order/product data (decision 2026-11-24, Jad)

Showing order and product data to the LLM steps is a deliberate departure from the original design (name-only context, all order data via tools). Jad chose it for customer experience — answering accurately about the customer's own orders — and accepted the risk that the agent works out business logic (eligibility, return windows, deadlines, warranty coverage) from that data instead of taking it from the tools.

Already seen: UI test conversation 5 — the agent counted a 30-day return deadline from the PLACED date of an order that hadn't been delivered. Mitigations in place: eligibility results carry the window facts (`return_window_days`, `return_deadline`, `return_window_starts`); the response prompt says delivery/tracking details and return deadlines come only from tool results; the output guard verifies claims against the same order list.

When reviewing eval runs or UI tests, watch for the agent stating eligibility, windows or dates it computed itself. If it keeps happening, bring it to Jad as a design question (revisit what the steps see) — don't patch it with more prompt rules on your own.
