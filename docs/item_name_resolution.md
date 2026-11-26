# Item-Name Resolution for Action Tools

**Status:** SUPERSEDED 2026-11-24 — do not implement this design.  
Solved differently on main (tag `decision/recent-orders-in-context`): the LLM steps see the
customer's recent orders and the classifier resolves product names to an `order_id`; tools
with no `order_id` use the only order or return `order_id_required` + `candidate_orders`.
See `.claude/rules/order-context.md` and BUILD_SPEC. Kept for the problem analysis only.  
**Filed:** 2026-04-19  
**Area:** `backend/tools/order_tools.py`, `backend/agents/conversation.py`,
`backend/routers/chat.py`, `prompts/production.yaml`

---

## 1. Problem and motivation

Customers naturally refer to purchases by item name, not order ID:

> "Cancel my earphones." / "Return the jacket." / "Refund the headphones I bought last week."

Every action tool in the current architecture requires `order_id`. When the classifier
extracts `item_name` without `order_id`, the tool immediately rejects with
`order_id_required` and the conversation dead-ends. Two observed failure modes from
production traces:

- **KB-021** (headphones, no order_id spoken): tool rejects on `order_id_required`,
  pass-2 LLM receives only the rejection, leaks chain-of-thought reasoning into the
  customer response ("Wait, I should follow my instructions..."), output guard fires
  `llm_escalation_intent`, customer gets a hollow escalation with no policy explanation.

- **ES-021** (blender, no order_id spoken): same `order_id_required` dead-end, agent
  escalates without explaining the defective-item warranty path. Persistently failing
  in all historical eval runs.

The fix: write tools accept an optional `item_name` parameter, resolve it to an order
internally, and return a structured result that lets the agent ask for all missing
information in one natural message instead of a sequential interrogation.

---

## 2. Tools in scope

### Write tools (primary change)

| Tool | Change | Rationale |
|------|--------|-----------|
| `cancel_order` | Add `item_name` param + resolution logic | Primary dead-end case |
| `initiate_return` | Add `item_name` param + resolution logic | Primary dead-end case; defective path |

### Read/check tools (additive filtering)

| Tool | Change | Rationale |
|------|--------|-----------|
| `check_cancel_eligibility` | Add `item_name` param | Already handles `order_id=None` by returning all eligible orders; `item_name` narrows results |
| `check_return_eligibility` | Add `item_name` param | Same — additive filter, no gate sequence change |
| `track_order` | Add `item_name` param | "Where are my headphones?" should work |
| `get_refund_status` | Add `item_name` param | "Where's the refund for my jacket?" should work |
| `check_missing_package` | Add `item_name` param | Consistency; "my blender never arrived" |

Read tools use `item_name` only for filtering. When `item_name` resolves to multiple
orders they return the full list; when it resolves to zero they return an empty result
with `reason: "item_not_found"`. No multi-missing bundling — read tools don't have a
gate sequence.

---

## 3. Item matching algorithm

**Algorithm: token intersection (no synonyms).**

```
1. Tokenize customer's item_name: lowercase, split on whitespace/punctuation,
   drop stop words: {the, my, a, an, this, it, order, one, that, some}.

2. For each product in the customer's orders, tokenize Product.name the same way.

3. Score = len(customer_tokens ∩ product_tokens).

4. Candidates: all products where score ≥ 1.

5. If one candidate:   resolved — continue with that order.
   If multiple:        multiple_matches — return list to agent.
   If zero:            item_not_found — agent asks for order number.
```

**Why no synonym table.** A synonym table ("earphones" → "headphones") is a maintenance
burden that grows with the product catalog. Silent synonym gaps — where a missing entry
causes a missed match with no visible error — are worse than an explicit
"I couldn't find that item" response that prompts the customer for their order number.
The `item_not_found` path is cheap and correct. Synonyms are excluded from v1.

**Edge cases:**

| Case | Behavior |
|------|----------|
| "red jacket" vs "blue jacket" (both in orders) | `multiple_matches` — agent presents both |
| Typo: "heaphones" | `item_not_found` — agent asks for order number |
| Category name: "the electronics one" | `item_not_found` — no product name token match |
| Single generic token: "item" | Likely matches everything — treated as `multiple_matches` |
| `order_id` + `item_name` both provided | `order_id` wins; `item_name` ignored entirely |

---

## 4. Tool contracts — rejection shapes

### 4a. Existing fields (unchanged)

These fields continue to appear exactly as today and carry the same meaning:

| Field | When present |
|-------|-------------|
| `check_kb: true` | Tool confirmed structural eligibility; policy conditions need KB lookup |
| `requires_escalation: true` | Defective/duplicate claims; human handling required |
| `in_warranty`, `in_return_window`, `warranty_months` | Defective path context for response generation |
| `confirmation_required: true` | Customer must confirm before action executes |
| `available_action` | Loop decision hint: a different tool should be called next |

### 4b. New fields

**`resolved` object** — added to any rejection where item resolution succeeded.
Carries what the tool already figured out so the agent can reference it and
`conversation.py` can persist it.

```json
{
  "order_id": "KB021-ORD",
  "item_name": "headphones",
  "item_match": "Wireless Headphones"
}
```

- `item_name`: raw customer string as extracted by the classifier
- `item_match`: `Product.name` as it appears in the database (used for customer-facing references)

### 4c. New reason codes

**`missing_info`** — item resolved, but required parameters are still absent.

```json
{
  "success": false,
  "reason": "missing_info",
  "missing": ["reason"],
  "resolved": {
    "order_id": "KB021-ORD",
    "item_name": "headphones",
    "item_match": "Wireless Headphones"
  }
}
```

`missing` is an array of string keys. For `cancel_order` it can contain `["reason"]`
(eligibility passes, reason not provided). For `initiate_return` it contains `["reason"]`
after eligibility passes. The array exists for forward compatibility but in v1 it
always contains exactly one element — bundling multiple missing fields in a single
rejection is a future extension, not a v1 requirement.

**`multiple_matches`** — item_name matched more than one order. Distinct from
`missing_info` because the agent's response pattern is "here are your options, which
one?" rather than "I found it, I just need X."

```json
{
  "success": false,
  "reason": "multiple_matches",
  "matches": [
    {
      "order_id": "ORD-101",
      "item_match": "Blue Denim Jacket",
      "status": "delivered",
      "placed_at": "2026-03-10"
    },
    {
      "order_id": "ORD-055",
      "item_match": "Blue Denim Jacket",
      "status": "delivered",
      "placed_at": "2025-11-20"
    }
  ]
}
```

**`item_not_found`** — no order in the customer's account contains a matching product.

```json
{
  "success": false,
  "reason": "item_not_found",
  "item_name": "earphones"
}
```

### 4d. Write-tool gate sequences with item_name

**`cancel_order`** (eligibility → reason → confirmation):

```
Step 0: order_id absent, item_name present → resolve.
        - Zero matches → item_not_found
        - Multiple matches → multiple_matches
        - One match → set order = matched order, continue.

Step 1: eligibility check (unchanged).
        - Ineligible → existing rejection shapes (already_cancelled, wrong_status, etc.)

Step 2: reason absent → {reason: "missing_info", missing: ["reason"],
        resolved: {order_id, item_name, item_match}}
        (eligibility passed; return resolved so agent can name the item)

Step 3: confirmation gate (unchanged).
        - No prior confirmation → confirmation_required with resolved included
        - Prior confirmation present → execute.
```

**`initiate_return`** (eligibility-with-reason → confirmation):

```
Step 0: order_id absent, item_name present → resolve. Same as above.

Step 1: eligibility check (reason-aware).
        - Defective → requires_escalation: true, check_kb: true, in_warranty,
          in_return_window, warranty_months — PLUS resolved: {order_id, item_name, item_match}
          (see §7 — resolved allows response prompt to name the item before escalating)
        - Other ineligible → existing rejections.

Step 2: reason absent → {reason: "missing_info", missing: ["reason"],
        resolved: {order_id, item_name, item_match}}

Step 3: confirmation gate (unchanged).
```

---

## 5. Classifier prompt changes

The classifier currently extracts `order_id` and `reason` per tool. Three additions are
needed.

### 5a. Add `item_name` to each tool's Extract line

For all tools that take `order_id`, add:

> `item_name` — the specific item as the customer described it; extract only when the
> customer names an item **and has not provided an order_id**. Set to null when
> order_id is present. Set to null when the customer hasn't named a specific item
> ("cancel my order", "track it").

### 5b. Precedence rule in `<selection_principles>`

Add after "Extract only what was stated":

> When the customer provides both an order ID and an item name in the same message,
> extract the order_id and set item_name to null. The tool uses order_id directly
> and ignores item_name when both are present.

### 5c. Over-extraction risk

The classifier may pull `item_name` from incidental context:
> "I've been using my headphones and they're great, but I want to return order #1234."

The "Extract only what was stated" principle guards against this for `order_id` today —
extending it to `item_name` and the new precedence rule provides the same guard.
Monitor with an IC eval case: `item_name` present in conversation but order_id also
present → classifier must return `order_id`, `item_name: null`.

---

## 6. Loop decision prompt changes

The loop decision LLM currently handles `available_action`, `confirmation_required`,
`requires_escalation`, `check_kb`, and hard rejections. Two new signals need examples.

### 6a. `missing_info` with `resolved`

```
Context: initiate_return returned missing_info, resolved: {order_id: "KB021-ORD",
  item_match: "Wireless Headphones"}, missing: ["reason"].
Customer message: "I want to return my headphones, they stopped working."

Reasoning: Item is resolved, order is known. Customer provided "stopped working"
which maps to defective. Call initiate_return with the resolved order_id and reason.
{"next": "action", "action": "initiate_return", "params": {"order_id": "KB021-ORD", "reason": "defective"}}
```

### 6b. `multiple_matches` disambiguation turn

```
Context: cancel_order returned multiple_matches for "jacket":
  [{order_id: "ORD-101", placed_at: "2026-03-10"}, {order_id: "ORD-055", placed_at: "2025-11-20"}].
  Agent asked customer to identify which order.
Customer message: "the March one"

Reasoning: Agent presented two options. Customer picked "the March one" which
corresponds to ORD-101 (March 10). Call cancel_order with the resolved order_id.
{"next": "action", "action": "cancel_order", "params": {"order_id": "ORD-101", "reason": null}}
```

Note: this disambiguation happens at the loop decision level, not the classifier level.
The classifier is not expected to resolve "the March one" to an order_id — it classifies
the turn as `action_request` for `cancel_order`. The loop decision LLM has access to
`pending_matches` in state (see §8b) and is the right place to do the resolution.

---

## 7. Response prompt changes

Three new patterns in `<tool_result_handling>`.

### 7a. `missing_info` — item resolved, one piece missing

```
missing_info with resolved — the tool matched the customer's item to a specific
order but is still missing information (typically a reason). Reference the item by
name, confirm you found the right one, and ask for only what's missing in one message.

Good: "I found your Wireless Headphones from order KB021-ORD — what's the reason for
the return? For example: it stopped working, it's not as described, or you changed
your mind."

Do not ask multiple questions in a row. If reason is the missing piece, ask for
reason only.
```

### 7b. `multiple_matches` — agent presents options

```
multiple_matches — the item name matched more than one order. Present the options
with enough detail for the customer to identify the right one: item name, order ID,
and date placed or delivered. Keep it concise.

Good: "I see two orders with a jacket — Order ORD-101 from March 10 (delivered) and
Order ORD-055 from November 20 (delivered). Which one did you mean?"

Always include the order ID explicitly in your response so the customer can confirm
it in their reply.
```

The instruction to always include order_id in the response is load-bearing: it ensures
the classifier can re-extract the order_id from prior-turn context when the customer
replies "the March one" or "the first one." The loop decision handles the case where
the classifier still can't extract it (see §9).

### 7c. `item_not_found`

```
item_not_found — the tool couldn't find an order on the customer's account matching
that item. Tell the customer you couldn't locate it and ask for their order number.
Do not guess or suggest other items.

Good: "I wasn't able to find an order for earphones on your account — do you have
the order number handy?"
```

### 7d. `requires_escalation` with `resolved` — defective path

When a tool result contains both `requires_escalation: true` and `resolved`, the
existing escalation response pattern applies but the agent must reference the resolved
item/order before pivoting to the policy explanation and handoff. Add to the existing
`requires_escalation` pattern:

```
When the result includes a resolved object alongside requires_escalation, open your
response by confirming which item and order you're looking at, then explain the policy
and hand off:

"Your Wireless Headphones from order KB021-ORD are outside the 14-day return window
but still under the 6-month warranty, so this goes through a warranty claim rather
than a standard return. [Policy details.] I need to hand this off to a specialist —
let me connect you now."

Do not skip the item confirmation step and jump directly to the handoff.
```

---

## 8. State design

### 8a. `AgentState` additions

Two new fields in `AgentState` (TypedDict in `backend/agents/state.py`):

```python
resolved_item: dict        # {order_id, item_name, item_match} from last missing_info result;
                           # empty dict {} when no resolution is pending.
pending_matches: list      # [{order_id, item_match, status, placed_at}] from last
                           # multiple_matches result; empty list [] when not pending.
```

Both default to empty (`{}` and `[]`). LangGraph silently drops `None` state
updates — use empty container sentinels, not `None`.

### 8b. `turn_state` additions

`turn_state` (JSONB on `Conversation`) currently persists:
`{actions_taken, consecutive_blocks, last_clarification_source}`.

Add:

```python
"resolved_item":    {},    # mirrors AgentState.resolved_item
"pending_matches":  [],    # mirrors AgentState.pending_matches
```

Written at turn end in `chat.py` alongside the existing fields.  
Read back at turn start into `initial_state`:

```python
"resolved_item":   prior_turn_state.get("resolved_item", {}),
"pending_matches": prior_turn_state.get("pending_matches", []),
```

### 8c. Lifecycle — when each field is set and cleared

**`resolved_item`**

| Event | Action |
|-------|--------|
| Tool returns `missing_info` with `resolved` | Set `resolved_item = resolved` |
| Tool returns `multiple_matches` | Clear `resolved_item = {}` |
| Tool call succeeds | Clear `resolved_item = {}` |
| Tool returns `requires_escalation` | Clear `resolved_item = {}` (escalation clears all turn_state) |
| Conversation escalates | `turn_state = None` (existing behavior) |
| Customer changes subject (classifier routes to non-action intent) | Clear `resolved_item = {}` |
| TTL: 3 turns without a successful tool call | Clear `resolved_item = {}` |

TTL implementation: `resolved_item` carries a `set_at_turn` counter. `chat.py` tracks
the current turn number. When the gap between `set_at_turn` and current turn exceeds 3,
`resolved_item` is cleared before building `initial_state`. Turn counter already exists
implicitly in `message` count — derive it from `len(messages) // 2` or add an explicit
`turn_number` to `turn_state`.

**`pending_matches`**

| Event | Action |
|-------|--------|
| Tool returns `multiple_matches` | Set `pending_matches = matches` |
| Customer's reply is successfully routed to a tool call | Clear `pending_matches = []` |
| Tool call succeeds | Clear `pending_matches = []` |
| Conversation escalates | `turn_state = None` |
| TTL: same 3-turn policy as `resolved_item` | Clear `pending_matches = []` |

### 8d. Interaction with existing turn_state fields

- `prior_turn_actions` — used by the confirmation gate. Unaffected. `resolved_item`
  and `pending_matches` are parallel fields, not replacements.
- `consecutive_blocks` — unaffected.
- `last_clarification_source` — unaffected.
- On escalation, `turn_state = None` clears everything including the new fields.
  This is correct — a resolved item or pending match list is meaningless after escalation.

---

## 9. Multiple-matches multi-turn flow (step by step)

### Turn 1

**Customer:** "Cancel my jacket."

**Classifier output:**
```json
{"intent": "action_request", "action": "cancel_order",
 "params": {"order_id": null, "item_name": "jacket", "reason": null}}
```

**Tool call:** `cancel_order(item_name="jacket", order_id=None)`

**Tool result:**
```json
{
  "success": false,
  "reason": "multiple_matches",
  "matches": [
    {"order_id": "ORD-101", "item_match": "Blue Denim Jacket", "status": "delivered", "placed_at": "2026-03-10"},
    {"order_id": "ORD-055", "item_match": "Blue Denim Jacket", "status": "delivered", "placed_at": "2025-11-20"}
  ]
}
```

**Loop decision:** `{"next": "respond"}` — `multiple_matches` is a "respond" signal.

**`conversation.py` sets:** `AgentState.pending_matches = [...]`, `AgentState.resolved_item = {}`

**Agent response:** "I see two orders with a Blue Denim Jacket — Order ORD-101 from
March 10 (delivered) and Order ORD-055 from November 20 (delivered). Which one did
you want to cancel?"

**`turn_state` persisted:**
```json
{
  "actions_taken": [...],
  "pending_matches": [
    {"order_id": "ORD-101", ...},
    {"order_id": "ORD-055", ...}
  ],
  "resolved_item": {},
  "consecutive_blocks": 0,
  "last_clarification_source": ""
}
```

---

### Turn 2a — customer replies with an explicit order ID

**Customer:** "ORD-101"

**Classifier:** The prior agent turn mentioned "Order ORD-101" explicitly.
The classifier's existing "use prior turns for context" instruction resolves
"ORD-101" to `order_id: "ORD-101"`. Output:
```json
{"intent": "action_request", "action": "cancel_order",
 "params": {"order_id": "ORD-101", "reason": null}}
```

**`conversation.py` reads** `pending_matches` from turn_state — still present.

**Tool call:** `cancel_order(order_id="ORD-101")` — order_id explicit, item_name not
needed.

**Gate continues normally.** `pending_matches` cleared when tool call is made.

---

### Turn 2b — customer replies with a natural-language disambiguation

**Customer:** "the March one"

**Classifier:** May or may not extract `order_id: "ORD-101"` depending on how
prominently the prior agent turn mentioned "ORD-101 from March 10." This is the
fragile path.

**If classifier extracts `order_id: "ORD-101"`:** Same as Turn 2a above.

**If classifier returns `order_id: null, item_name: "jacket"` (fails to disambiguate):**

- Tool is called with `item_name="jacket"`, `order_id=None`
- Tool resolves `item_name` again and returns `multiple_matches` again
- **But this time:** the loop decision LLM has `pending_matches` in state context and
  the customer's "the March one" as the message — loop decision example (§6b) fires
- Loop decision resolves to `{"next": "action", "action": "cancel_order", "params": {"order_id": "ORD-101"}}`
- Turn 3 tool call proceeds with `order_id: "ORD-101"`

**Relies on classifier:** Agent response always includes order IDs explicitly (§7b
instruction). This is the primary disambiguation mechanism and should work for most
replies ("ORD-101", "the first one", "March").

**Relies on loop decision:** Fallback for natural-language replies that don't contain
enough signal for the classifier. Loop decision has `pending_matches` in state and an
explicit example showing the resolution. This is the right layer — the loop decision
LLM already interprets tool result context to make routing decisions; this is the same
kind of reasoning.

**Final fallback:** if both fail (rare — would require the customer to respond
completely obliquely, e.g. "yes"), the tool returns `multiple_matches` a second time
and the agent asks the customer to be more specific or provide the order number.
This is an acceptable user experience.

**What the design does NOT rely on:** the classifier independently resolving natural
language date references ("March") to order IDs without the prior-turn context. The
agent's response is structured to make this easy for the classifier, but the loop
decision is the backstop.

---

## 10. Defective path with item_name — gate sequence

**Scenario:** Customer says "return my headphones, one earbud stopped working" — no
order_id. Mock state has an electronics order delivered 40 days ago with a 6-month
warranty.

**Classifier output:**
```json
{"intent": "action_request", "action": "initiate_return",
 "params": {"order_id": null, "item_name": "headphones", "reason": "defective"}}
```

**Tool call:** `initiate_return(item_name="headphones", reason="defective")`

**Step 0 (item resolution):** Matches "Wireless Headphones" in order KB021-ORD.

**Step 1 (eligibility, defective-aware):** `_check_return_eligibility_sync(order, products, reason="defective")`
→ 40 days > 14-day electronics window → defective path fires:
```
in_return_window: False
in_warranty: True
warranty_months: 6
requires_escalation: True
check_kb: True
```

**Tool result:**
```json
{
  "success": false,
  "reason": "defective",
  "requires_escalation": true,
  "check_kb": true,
  "in_return_window": false,
  "in_warranty": true,
  "warranty_months": 6,
  "resolved": {
    "order_id": "KB021-ORD",
    "item_name": "headphones",
    "item_match": "Wireless Headphones"
  }
}
```

**Does the agent confirm the item before escalating?** Yes — `resolved` is present in
the tool result alongside `requires_escalation`. The response prompt pattern in §7d
instructs the agent to open with "Your [item_match] from order [order_id]..." before
the policy explanation and handoff. The structural escalation fires from the output
guard (`mock_handoff_intent` in test mode, or `handoff_intent` from LLM in production)
but the response content is determined by the response generation LLM, which sees
`resolved` in the tool result and follows the prompt.

**The escalation does not short-circuit the item confirmation.** The tool returns a
result; it does not produce a customer-facing response. The response generation LLM has
full access to all tool result fields including `resolved`. Whether it uses them
correctly is a function of the response prompt instruction in §7d.

**`turn_state` after this turn:** `requires_escalation: true` → `turn_state = None`.
`resolved_item` is not persisted. Correct — the escalation path terminates the
conversation's active action tracking.

---

## 11. Eval cases in scope

### Cases expected to change verdict (fix target)

| Test ID | Sheet | Why |
|---------|-------|-----|
| KB-021 | KB Retrieval | Headphones, no order_id. Tool dead-ends on `order_id_required` today. With `item_name`, resolves to KB021-ORD and runs eligibility. |
| ES-021 | Escalation | Blender, no order_id. Same dead-end pattern. All historical runs fail. |

### Cases that will exercise new paths (need new eval cases, not updates)

1. `item_name` resolves uniquely → full gate through to confirmation (cancel or return)
2. `item_name` resolves uniquely + reason bundled → `missing_info` → second turn with reason → gate continues
3. `item_name` → `multiple_matches` → disambiguation turn → gate continues
4. `item_name` → `item_not_found` → agent asks for order number
5. `item_name` + `order_id` both in message → `order_id` wins, `item_name` ignored (IC regression case)
6. `item_name` + `reason=defective` → defective path → agent names the item, explains warranty, escalates

### Cases that test the existing confirmation gate (must not regress)

| Test IDs | Risk |
|----------|------|
| AE-002, AE-006, AE-023, AE-032, AE-033 | order_id provided; gate sequence unchanged for these |

### Cases with item name + order_id present (verify order_id takes precedence)

AE-021, AE-028, AE-033, AE-035 — all include item names alongside explicit order IDs.
With the new classifier behavior, these cases should produce `item_name: null` in
the classifier output. Verify the classifier doesn't over-extract `item_name` when
`order_id` is present.

### AE-026 (special case)

Customer names headphones in turn 1 (no order_id), provides order_id in turn 2. With
`item_name` resolution, the tool could resolve in turn 1 instead of waiting for the
order_id in turn 2. The expected behavior will need to explicitly permit both paths
("agent may resolve via item_name in turn 1 OR via order_id in turn 2 — both valid").

---

## 12. Rollout plan

### Step 1 — Tool layer
**Files:** `backend/tools/order_tools.py`

Add `_resolve_order_by_item_name(customer_id, item_name, db)` helper. Add `item_name`
param to all seven tools. Write tools: add Step 0 (resolution) before existing gate
sequence. Read tools: add item_name filtering after the no-order_id all-orders branch.
Add `resolved` field to relevant rejection returns.

Existing `order_id` path is structurally unchanged — `item_name` is only consulted
when `order_id` is `None`. All existing tests must pass without modification.

### Step 2 — Unit tests
**Files:** `tests/test_tools/`

Test `_resolve_order_by_item_name` directly: exact match, partial match, multiple
matches, no match, order_id-takes-precedence. Test each new rejection shape. Confirm
gate sequence is preserved for existing order_id paths. Run full AE and ES eval sheets
against the new tool layer before any prompt changes.

### Step 3 — State schema
**Files:** `backend/agents/state.py`, `backend/routers/chat.py`

Add `resolved_item: dict` and `pending_matches: list` to `AgentState`. Add to
`initial_state` construction in `chat.py` (reading from `prior_turn_state`). Add to
`new_turn_state` written at turn end. Add TTL clearing logic.

No prompt changes yet — new state fields are inert until prompts reference them.

### Step 4 — Classifier prompt
**Files:** `prompts/production.yaml` (classifier section)

Add `item_name` to each tool's `Extract:` line. Add precedence rule. Add two new
examples: item_name without order_id; item_name + order_id present (order_id wins).
Run IC eval sheet.

### Step 5 — Loop decision prompt
**Files:** `prompts/production.yaml` (loop decision section)

Add `missing_info` + `multiple_matches` examples (§6a, §6b). Run AE and KB eval sheets.

### Step 6 — Response prompt
**Files:** `prompts/production.yaml` (response section)

Add `missing_info`, `multiple_matches`, `item_not_found`, and `requires_escalation +
resolved` patterns (§7a–d). Run full KB, AE, ES, PC sheets.

### Step 7 — Eval cases
**Files:** `evals/eval_test_cases.xlsx`

Update KB-021 and ES-021 expected behaviors. Add new cases for the six paths in §11.
Update AE-026 to permit both resolution paths.

### Commit structure
Tool layer as one commit, state schema as a second, each prompt section as a third,
eval updates as a fourth. Prompt changes should not ship until the tool layer has been
validated against the AE and ES sheets.

### Feature flag
Not needed. `item_name` defaults to `None` and the tool behavior for order_id-bearing
calls is structurally unchanged. The change is all-or-nothing within each layer but
each layer is backward-compatible.

---

## 13. Rejected approaches

### Classifier re-extraction of resolved order_id

Initial design proposed relying on the classifier to re-extract `order_id` from the
agent's prior-turn message on follow-up turns (e.g., agent says "I found order KB021-ORD"
→ classifier extracts `order_id: "KB021-ORD"` next turn). Rejected because:

- Depends on the agent mentioning the order_id naturally in the response
- Depends on classifier LLM picking it up from prior-turn context consistently
- No test-time visibility into whether extraction succeeded
- LLM drift across model versions could silently break it

Replaced with `turn_state.resolved_item` (§8) — deterministic, inspectable, not
dependent on LLM behavior at turn N+1.

### Synonym table (`_ITEM_SYNONYMS`)

Initial design included a Python dict mapping customer words to product name variants
(e.g., `"earphones" → {"headphones", "earbuds"}`). Rejected because:

- Table grows with the product catalog; gaps are invisible until a customer hits them
- Silent mismatch is worse than an explicit `item_not_found` that prompts for order number
- The `item_not_found → ask for order number → tool call with order_id` path is
  already implemented and works

Token intersection without synonyms is the v1 starting point. If synonym coverage
becomes a measurable problem (tracked via `item_not_found` rate in production), it can
be added in a focused follow-up.

---

## 14. Open questions for implementation time

1. **TTL counter implementation.** `resolved_item.set_at_turn` needs a turn counter.
   The cleanest source is `len(conversation.messages) // 2` (pairs of customer +
   agent messages). Verify this is available when `chat.py` builds `initial_state`,
   and that it stays stable across reconnects.

2. **Loop decision LLM access to `pending_matches`.** The loop decision prompt
   currently receives tool results via `action_results` in the context block. Verify
   that `pending_matches` from `AgentState` is surfaced to the loop decision LLM's
   context, or add it explicitly as a state-injection point in `conversation.py`.

3. **Classifier reliability on "the March one" disambiguation.** Described as relying
   on the agent's response including order IDs explicitly (§7b instruction). Should
   be verified with a targeted IC eval case before shipping Step 4. If the classifier
   fails more than ~20% of the time on natural-language date disambiguation, the loop
   decision fallback in §9 is the mitigation — but a classifier example showing the
   resolution would be cleaner.

4. **Read tool behavior on `multiple_matches`.** When `check_cancel_eligibility` is
   called with `item_name="jacket"` and two jacket orders exist, it should return
   both eligibility results in the list (the tool already returns all orders when
   `order_id=None`). Verify the item_name filter narrows correctly without
   accidentally dropping orders the customer might want.

5. **AE-026 expected behavior update.** Current expected behavior assumes the agent
   waits for the order_id to be provided in turn 2. With item_name resolution, the
   agent may proceed to the gate in turn 1. The eval case needs to explicitly permit
   both paths before the AE sheet is run against the new tool layer.
