# AI Support Agent — Pending Changes

Last updated: 2026-03-28

---

## Input guard improvements

### 1. Use a cheaper model for the input guard classifier
The AI classifier (Stage 2) currently uses Sonnet for every message, but it's just picking one of four categories (safe / prompt_injection / abusive / off_topic). Switch to Haiku or GPT-4o Mini — cuts that call's cost by 80-90%. Add a `LITELLM_GUARD_MODEL` setting in config. Validate with eval suite to confirm it still catches attacks.

**Eval action:** Re-run input guard eval sheet (sheet 1) with new model. Compare pass rates to Sonnet baseline — regression threshold: no more than 2% drop on prompt injection detection. If it regresses, stay on Sonnet for guards.

### 2. Log blocked attempts with a filterable field ✓ DONE 2026-03-28
When the input guard blocks a message, write an audit log entry with `action: "input_guard_blocked"` and the category (injection / abusive / off_topic) in output_data. Use the existing audit_logs table — no new table needed. This lets you query patterns after 100+ conversations ("how often are legit messages being blocked?") and debug individual cases on the fly.

**Eval action:** None — infrastructure/logging only, no behavior change.

### 3. Escalate after repeated blocks ✓ DONE 2026-03-28
If a customer gets redirected 3+ times in a row, hand to a human instead of looping. Right now a blocked customer just keeps getting the same polite redirect forever. A real customer who asked an off-topic question twice is getting frustrated — catch that and escalate.

Implementation: add a `consecutive_blocks` counter to `AgentState` in `state.py`. The conversation agent increments it when the input guard blocks a message, resets it to 0 on any successful (unblocked) turn. When the counter hits 3, route to escalation with reason `repeated_failure` instead of returning another redirect. This is simpler and faster than querying audit logs — no dependency on item 2.

Files: `backend/agents/state.py` (new field), `backend/agents/conversation.py` (increment/reset/check logic), `backend/routers/chat.py` (initialize field to 0)

**Eval action:** Add 2–3 eval cases: (a) customer blocked 3x in a row → verify escalation triggers with reason `repeated_failure`, (b) customer blocked twice then sends a valid message → verify counter resets and conversation continues normally. Add to safety/robustness tier.

### 4. Differentiate blocked response wording by category ✓ DONE 2026-03-28
Off-topic questions ("what's the weather?") should get a friendlier redirect than a prompt injection attempt ("ignore your instructions"). Currently they all get the same generic message.

**Eval action:** Add eval cases per category checking tone of redirect message: off-topic = friendly redirect, prompt injection = firm/neutral, abusive = brief + escalation path. Rubric: FAIL if tone is identical across categories or if off-topic gets a hostile redirect.

---

## Conversation agent improvements [DONE]

### 5. Add clarifying questions via prompting
If the customer's intent is ambiguous (e.g., "I want a refund" but they have 3 orders), the agent should ask which order instead of guessing or escalating. Implementation: update `INTENT_PROMPT` to classify ambiguous requests as `general` and generate a clarifying question. Cap at 1 clarifying question per turn — never two in a row. If the customer's response is still unclear after one question, proceed with best judgment or escalate.

File: `backend/agents/conversation.py` — edit `INTENT_PROMPT`

**Eval action:** Already implemented — verify existing eval cases cover: ambiguous request with multiple orders → agent asks which order (not guesses). Add cases if missing. Rubric: FAIL if agent guesses without asking, FAIL if agent asks more than 1 clarifying question per turn.

### 6. Distinguish "not enough info" from "genuinely can't help" in confidence handling
Two very different situations are currently treated the same (low confidence → escalate):
- "I don't have enough info yet" — customer said "refund please" with 3 orders. Solvable — ask a follow-up question.
- "I genuinely can't help" — customer is asking about something outside the KB or policies don't cover it. Escalate.

Update the intent classification prompt to distinguish between the two. "Not enough info" asks a question. "Can't help" escalates.

**Not just a prompt change.** The intent classifier currently returns one of four intents (`knowledge_query`, `action_request`, `escalation_request`, `general`). To handle "not enough info", add a fifth intent: `needs_clarification`. Then add a conditional in `conversation_agent_node` (Pass 1) to handle it — generate a clarifying question via `_generate_response` instead of routing to escalation. This is a small code change but it's real: without it, the new intent has nowhere to go.

Files: `backend/agents/conversation.py` — edit `INTENT_PROMPT` (add new intent + examples) AND add `needs_clarification` handling in `conversation_agent_node`

**Eval action:** Already implemented — verify eval cases cover both paths: (a) "not enough info" → agent asks a clarifying question (intent = `needs_clarification`), (b) "genuinely can't help" → agent escalates. Add cases if missing. Classification tier: test that intent classifier returns `needs_clarification` vs `escalation_request` correctly.

---

## Output guard improvements

### 9. Replace rule-based output guard with cheap LLM check ✓ DONE 2026-03-28
The current regex-based output guard is brittle — it only catches exact phrasings and misses creative wordings. Replace with a cheap LLM call (same `LITELLM_GUARD_MODEL` as #1) that checks: "Does the response claim any action that wasn't in the tools list? Does it contain any IDs not in the known set?" Catches every phrasing, every language. Cost: pennies per call on Haiku. Latency: 200-500ms, barely noticeable since the customer has been watching the response stream in.

File: `backend/guardrails/output_guard.py`

**Eval action:** Re-run output guard eval sheet. Expect major improvement from 44% baseline. Key cases: fabricated tracking numbers, cross-customer data leaks, system prompt disclosure, speculative claims. Also run regression on cases the rule-based guard already catches — no regressions allowed.

### 10. Log output guardrail triggers
When the output guard blocks a response, write an audit log entry with `action: "output_guard_blocked"`, `input_data` containing what the agent tried to say, and `output_data` containing what was wrong (which check failed, what was missing). Use the existing audit_logs table — same pattern as #2.

After 100+ conversations, query "all audit logs where action = output_guard_blocked, grouped by reason" to see patterns. This feeds directly into prompt improvement: if the agent keeps hallucinating cancellations, you know which part of the prompt to fix and can write targeted eval cases.

**Eval action:** None — infrastructure/logging only. But the logged data becomes input for writing new eval cases over time (production feedback loop).

### 14. Output guard: add impossible_promise detection ✓ DONE 2026-03-28 (superseded by #9)
The rule-based output guard (44% baseline pass rate) misses several failure categories: fabricated tracking numbers, cross-customer data leaks, system prompt disclosure, and speculative claims ("might arrive a day early"). These are all `impossible_promise` variants that regex can't catch reliably. This is a stop-gap — item #9 (LLM-based output guard) replaces the approach entirely, but documenting the specific gaps here feeds the eval cases and the LLM guard prompt when #9 is built.

File: `backend/guardrails/output_guard.py`

**Eval action:** Ensure eval cases already exist for each gap category (fabricated tracking numbers, cross-customer data leaks, system prompt disclosure, speculative claims). These cases will validate item #9 when implemented. If missing, add before baseline run.

---

## Escalation improvements

### 7. Check escalation summary template quality (manual testing task)
During manual testing: review the escalation summaries generated by `escalation.py`. Is the summary detailed enough for a human agent to pick up the conversation without re-reading the entire chat? If it's too thin, improve the template.

File: `backend/agents/escalation.py`

**Eval action:** Manual review task. After reviewing, add 1–2 eval cases testing escalation summary quality: does it include customer issue, actions already taken, and reason for escalation? Rubric: FAIL if a human agent would need to re-read the full chat to understand the situation.

### 8. Admin dashboard: escalation summary as quick briefing
In Phase 5 when building the admin dashboard: show the escalation summary at the top as a quick briefing so the human agent can get up to speed fast. Full chat history available below for context when they need to dig deeper. The API already returns both (`GET /api/conversations/{id}`).

**Eval action:** None — frontend/UI task, no agent behavior change.

### 13. Collapse escalation_handler into a pluggable async function
The escalation handler is currently a separate LangGraph node. Collapse it into a pluggable async function called directly from `conversation_agent_node`. Define a simple interface: `async callable(reason, context) → handoff_message`. Default implementation logs to DB + returns template message. Graph routes straight to END after escalation. This is a simplification + pluggability improvement, not a latency win.

Files: `backend/agents/graph.py` (remove node, update edges), `backend/agents/conversation.py` (call escalation function directly), `backend/agents/escalation.py` (refactor to async callable)

**Eval action:** Regression-only — re-run all escalation eval cases after refactor. No new cases needed since behavior is unchanged. FAIL if any escalation case that previously passed now breaks.

---

## Tool improvements

### 11. Enrich tool descriptions (prompt engineering for tools)
Tool descriptions are injected into the LLM's context — they're effectively part of the prompt. The current descriptions for `track_order`, `cancel_order`, and `process_refund` are minimal. Enrich each with: preconditions (what must be true before calling), edge cases (partial refunds, already-cancelled orders), and when NOT to use the tool (e.g., don't use `process_refund` for exchanges). This changes LLM behavior without touching any code. High leverage once an eval suite is in place to measure the impact.

File: `backend/tools/registry.py`

**Eval action:** Add eval cases for tool edge cases: partial refund request, cancel an already-cancelled order, refund request that's actually an exchange. Rubric: FAIL if agent calls wrong tool or calls tool without checking preconditions. Before/after comparison — run these cases before and after enriching descriptions to measure impact.

### 12. Use cheap model for intent classification
`_classify_intent` currently uses the main `litellm_model` (Sonnet), but it's a JSON classification task — same category as the guards. Either reuse `LITELLM_GUARD_MODEL` from item 1 or add a separate `LITELLM_CLASSIFIER_MODEL` setting. Same cost savings logic as the guards: Sonnet is overkill for returning `{"intent": "knowledge_query", "confidence": 0.9}`.

File: `backend/agents/conversation.py` (switch model in `_classify_intent`), `backend/config.py` (new setting if separate from guard model)

**Eval action:** Re-run classification eval sheet with new model. Compare intent accuracy to Sonnet baseline — regression threshold: no more than 3% drop on any intent category. Pay special attention to `needs_clarification` vs `escalation_request` distinction (item 6).

---

## Implementation notes

- Items 1 and 9 share the same model setting (`LITELLM_GUARD_MODEL`) — implement together
- Items 2 and 10 share the same pattern (audit log with filterable action field) — implement together
- Items 5 and 6 are both changes to `INTENT_PROMPT` — item 5 is prompt-only, item 6 also needs a new intent handler in `conversation_agent_node`
- Item 3 adds a `consecutive_blocks` field to `AgentState` — no dependency on item 2
- Item 7 is a manual testing task, not a code change
- Item 8 is a Phase 5 frontend task
- Item 11 is high leverage but depends on having an eval suite to measure impact *(eval suite now built — item 11 is unblocked)*
- Item 12 extends item 1's model setting to also cover intent classification — implement together
- Item 13 is a refactor — no new functionality, just simpler graph structure
- Item 14 is a stop-gap; superseded by item 9 once implemented. Useful for documenting specific gaps that the LLM guard prompt should cover