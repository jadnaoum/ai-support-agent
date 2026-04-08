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
