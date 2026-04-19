# Tech Debt

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
