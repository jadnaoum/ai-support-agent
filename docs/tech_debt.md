# Tech Debt

## ~~Tools raise on non-UUID order_id~~ GUARDED 2026-09-04 (option 1 shipped; options 2-3 open)

`_find_order` in order_tools.py treats anything not UUID-shaped as not found, so the agent asks for the order number instead of escalating. Jad is adding the order_id format to the intent prompt (eval fixtures with short ids will then need UUID-shaped ids). Item-name resolution (option 2) is still the real fix. Original entry kept below.

## Tools raise on non-UUID order_id (found 2026-09-02 via the demo UI)

**Area:** `backend/tools/order_tools.py` — every tool that takes `order_id`
(`check_cancel_eligibility` reproduced; `cancel_order`, `track_order`,
`check_return_eligibility`, `initiate_return`, `get_refund_status` take the same path).

### Problem
When the classifier passes a product name or phrase as `order_id` (customer said
"the SoundMax headphones order, the only one I have"), the tool runs
`select(Order).where(Order.id == order_id)` and asyncpg raises
`DataError: invalid input for query argument $1 … (invalid UUID)`. `action_service`
catches it as `unhandled_error`, and the turn escalates to a human with reason
`unhandled_error` — for a customer who simply named their product. Nothing is logged
server-side (the exception is swallowed into the result dict), so it is invisible
outside the demo's timeline.

Reproduce (any DB with a customer):
```
TOOL_REGISTRY["check_cancel_eligibility"].handler(db=db, customer_id=cid, actions_taken=[], order_id="headphones")
```

### Options (prompt-owner decision — changes what the agent does)
1. Validate `order_id` format at tool entry and return a structured rejection
   (`reason: "order_not_found"` or a new `invalid_order_id`) so the agent asks
   for the order number instead of escalating. Smallest change; eval sheets with
   order-ID-required cases need re-checking.
2. Item-name resolution (docs/item_name_resolution.md, ARCHITECTURE "designed,
   deferred"): resolve product names to the customer's orders before the DB lookup.
   The real fix; larger.
3. At minimum, `action_service` should `logger.exception` the swallowed error.


## ~~run_graceful_failure should read mock_account_state~~ RESOLVED 2026-09-01

The runner now reads `mock_account_state` (evals/run_evals.py `run_graceful_failure`), the
Graceful Failure sheet has a `mock_account_state` column, GF-002 carries
`{"mock_handoff_intent": true}`, and its judge_rubric includes the structural
escalation check (same IMPORTANT pattern as ES cases). Original entry kept below for context.

## run_graceful_failure should read mock_account_state

**Filed:** 2026-04-19
**Area:** `evals/run_evals.py` — `run_graceful_failure()`

### Problem
`run_graceful_failure` hardcodes `mock_context={}` when calling `_call_agent_full`, so it never reads
`mock_account_state` from the sheet:

```python
# evals/run_evals.py ~line 441
agent_resp = _call_agent_full(messages, {}, test_id=test_id, version_tag=version_tag,
                               mock_agent_state=mock_agent_state)
```

Every other runner (`run_escalation`, `run_action_execution`, `run_kb_retrieval`, `run_policy_compliance`,
`run_context_retention`) reads `mock_context = _parse_json_field(test_case.get("mock_account_state"))` and
passes it through.

### Impact
GF-002 is a Path 5 eval case (KB returns no results → agent should escalate). Adding
`mock_handoff_intent: true` to its `mock_account_state` is the correct architectural classification, but
the flag is never sent to the API — `run_graceful_failure` drops it. The rubric structural check
(`requires_escalation flag is True`) therefore cannot pass for GF-002 until this is fixed.

### Fix
In `run_graceful_failure`, replace the hardcoded `{}` with the same pattern the other runners use:

```python
async def run_graceful_failure(test_case, calibrate, test_id="", version_tag=""):
    messages = _parse_conversation(test_case.get("conversation"))
    mock_context = _parse_json_field(test_case.get("mock_account_state"))   # add this
    mock_agent_state = _parse_json_field(test_case.get("mock_agent_state"))
    agent_resp = _call_agent_full(messages, mock_context, test_id=test_id, version_tag=version_tag,
                                   mock_agent_state=mock_agent_state)
```

Then: add `mock_account_state` column to the Graceful Failure sheet, set `{"mock_handoff_intent": true}`
on GF-002, and update GF-002's `judge_rubric` to add the structural escalation check (same IMPORTANT note
pattern as ES cases).
