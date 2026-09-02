# Frontend Demo Experience — Critique and Implementation Plan

Source directive: `~/Downloads/CC_DIRECTIVE_FRONTEND_DEMO.md` (2026-09-02).
Status: **awaiting approval**. No code has been written.

---

## Part A — Answers to the directive's five questions

### 1. What does the current SSE stream return?

`GET /api/chat/stream/{conversation_id}` (`backend/routers/chat.py:159`) uses sse-starlette
`ServerSentEvent(data, event)`. Three event types today:

| event | data | when |
|---|---|---|
| `token` | one word + trailing space | after the whole graph finishes |
| `escalated` | empty | if the turn escalated, or the conversation is already escalated |
| `done` | empty | always last |

The graph runs via `graph.ainvoke()` under a 90 s `asyncio.wait_for`. **Nothing is emitted until
the graph completes**; token "streaming" is the finished response split on spaces (PENDING #18).
There is no greeting turn: the endpoint returns 422 when no customer message exists.

### 2. Does `POST /api/csat` exist?

Yes, in `backend/routers/webhooks.py`. Schema: `{conversation_id, score (1-5), comment?}`.
Stores to `conversations.csat_score` / `csat_comment` — **there is no CSAT table**. It rejects
active conversations (400) and duplicate ratings (409). No tags column. Covered by
`tests/test_routers/test_webhooks.py` (12 tests).

**Consequence:** the frontend's "End chat" button needs a way to move the conversation to
`resolved` before CSAT is accepted. No such endpoint exists today.

### 3. Does `GET /api/products` exist?

No. `GET /api/customers` exists (admin router). Products are only reachable via ORM.

### 4. What LangGraph hooks are available mid-execution?

LangGraph **0.2.60** exposes `astream(stream_mode="values"|"updates"|"debug")` and
`astream_events`. It has the `StreamWriter` type but **no `get_stream_writer` / `"custom"`
stream mode**. Node-level `updates` would surface `knowledge_service` and `action_service`
completions, but both guards run *inside* `conversation_agent_node`, and their verdicts are not
stored in `AgentState` — so `updates` mode cannot produce `input_guard` / `output_guard` events.
A side channel is required (see Part C, design decision 2).

### 5. Is cost data accessible in the chat flow today?

**No, and the directive's premise is wrong.** The eval framework only computes cost for *judge*
calls (`evals/judges/safety.py` via `litellm.completion_cost`). Agent-side cost in
`/api/chat/test` is a `chars ÷ 4 + 700` estimate with an explicit comment "we can't hook into
graph internals". Additionally `evals/config.py` notes `completion_cost()` does not resolve the
short model ID `claude-sonnet-4-6` reliably, so a fallback price table is required.
This is new instrumentation, not a re-emit.

---

## Part B — Critique (scope changes and errors in the directive)

1. **shadcn/ui is not the existing stack.** `frontend/package.json` has only react + react-dom.
   No shadcn, no `components.json`, no `@/` alias, no react-router, bare Tailwind config, JSX not
   TSX. ARCHITECTURE.md *decided* on shadcn but it was never set up. Either init it now (adds
   ~6 deps + config) or hand-roll Tailwind components like the current `App.jsx`.

2. **Phantom tool names.** `check_refund_eligibility` and `process_refund` do not exist in
   `TOOL_REGISTRY`. The 7 real tools: `track_order`, `cancel_order`, `check_cancel_eligibility`,
   `check_return_eligibility`, `initiate_return`, `check_missing_package`, `get_refund_status`.
   The "confirmation before state change" check must key on `cancel_order` and `initiate_return`.

3. **The example business rules are wrong.** "Refunds over €50 → escalated to human" is false:
   returns on orders over €50 go to `pending_review` (tool returns `success: true,
   reason: return_pending_review`) — no escalation. "Email/account changes → always escalated"
   has no tool behind it; it's a KB/business-limitations path. The rules panel must describe
   what the tools actually do.

4. **There is no policy store to pull rules from.** ARCHITECTURE.md describes `policies.yaml`
   as the source of truth, but it does not exist. Rules live in three places: code constants in
   `order_tools.py` (the `> 50` literal, `_NON_RETURNABLE_CATEGORIES`, status rules,
   defective/duplicate escalation), per-product DB columns (`return_window_days`,
   `warranty_months`, `final_sale`), and KB markdown. "Query the policy data" is only partly
   possible; the rest must be exposed from code constants. See decision 3.

5. **The greeting turn does not exist.** The agent never speaks first. Either the frontend shows
   a static greeting bubble, or a backend greeting turn is built (that would need a prompt path
   that doesn't exist — prompt-owner work). See decision 4.

6. **Escalation is not covered at all.** The agent can escalate mid-conversation (abusive input,
   3 blocks, output guard, defective claim, customer request, guard error). After that the stream
   short-circuits and the input must close. The chat page needs: banner, input closed, and the
   CSAT flow triggered automatically (status is already non-active, so CSAT is accepted).

7. **Two of the five summary checks are text heuristics dressed as deterministic.**
   "Agent mentioned policy" and "action-related agent response" require regexing the response
   text, which is exactly the brittleness the directive's "no LLM-as-judge" rule is trying to
   avoid. Proposed structural replacements in decision 5.

8. **Colour logic contradicts itself.** Tool rows are "amber always", but the colour section
   says purple for "tool rejected ineligible request" *and* amber for "tool returned not
   eligible". One mapping proposed in decision 6.

9. **Preset order count vs endpoint cap.** Presets generate 1–4 orders; the endpoint caps at 3.
   Presets will generate 1–3.

10. **Email collision.** `customers.email` is UNIQUE. The second Gandalf would 500 on
    `gandalf@shire.com`. Emails will be `gandalf-a1b2c3@shire.demo`.

11. **PII check vs the customer's own email.** `get_customer_context` puts the customer's email
    into agent state, so the agent may legitimately say "we'll email the label to
    gandalf-a1b2c3@shire.demo". A naive email regex flags correct behaviour. See decision 7.

12. **`delivered_at` must be set** on delivered/returned demo orders, or the return-window check
    is silently skipped (`_delivered_date()` returns None → eligible). Returned orders also need a
    `Refund` row so `get_refund_status` has something to show.

13. **Throughput ceiling for a public link.** Tier-1 limits (30k input tokens/min, 50 RPM) with
    ~4 Sonnet calls per turn give roughly 2–3 customer messages per minute *across all visitors*
    (PENDING #16/#19). Not a frontend problem, but 429s will happen on demo day; the UI will
    render them as a red "system error" step with a friendly message. Flagging so it's not a
    surprise.

14. **Minor:** "Total messages" is undefined — proposed: number of customer messages sent.
    Reloading `/chat/:customerId` loses in-memory turns/events — acceptable for a demo; reload
    starts a fresh conversation for the same customer.

---

## Part C — Design decisions needing Jad's call

Each has a recommendation. Reply with the number and a choice, or "all as recommended".

**1. Component library.** (a) *Recommended:* run `shadcn init` (JS mode), pull only Button,
Card, Select, Badge, Textarea, Collapsible; adds react-router-dom, lucide-react, cva, clsx,
tailwind-merge, `@/` alias. (b) Hand-rolled Tailwind in the style of today's `App.jsx`, plus
react-router-dom only.

**2. Event mechanism (touches prompt-adjacent files — asking per the rule).**
*Recommended:* a `TurnSink` object (asyncio.Queue + usage accumulator) held in a `ContextVar`,
set by the stream endpoint per request. Emit points are single lines, behaviour-neutral:
- `backend/guardrails/input_guard.py` — `emit("input_guard", ...)` at the return of `check_input`
- `backend/guardrails/output_guard.py` — `emit("output_guard", ...)` at the return of `check_output`
- `backend/agents/knowledge_service.py` — `emit("kb_retrieval", {articles})` in the two search paths + error path
- `backend/agents/action_service.py` — `emit("tool_call", {tool, result, order_id, requires_escalation, confirmation_required, duration_ms})`
- **Cost:** a `backend/llm.py` `acompletion()` wrapper that calls `litellm.acompletion`, then
  records `response.usage` + cost into the sink synchronously. The 5 call sites in
  `conversation.py` and 2 in the guards become an import swap only (no argument changes).
  Alternative: a LiteLLM `CustomLogger` callback — zero call-site edits but fire-and-forget,
  so the last call's usage can race `turn_complete`. Wrapper is deterministic; recommended.
- `emit()` is a no-op when no sink is set, so `/chat/test`, evals and unit tests are unaffected.
- No node ordering, routing, parsing or prompt text changes anywhere.

**3. Business-rules source.** (a) *Recommended:* `GET /api/demo/rules` composes one-liners from
what is real today — products table aggregated by category (return windows, warranty months,
final-sale products by name) plus code constants imported from `backend/tools` (move the `> 50`
literal to `RETURN_REVIEW_THRESHOLD_EUR` in `constants.py`; reuse `_NON_RETURNABLE_CATEGORIES`,
`ESCALATION_REASONS`, cancel-status rules). Changing a product row or a constant changes the
panel. (b) Create `policies.yaml` now and refactor tools to read it — the ARCHITECTURE plan,
but it's a tool refactor with eval impact; out of scope for this directive. Recommend (a),
log (b) in PENDING_CHANGES.

**4. Greeting.** *Recommended:* static frontend greeting bubble ("Hi {name}, I'm the support
assistant. What can I help with?") shown in the chat column; the timeline shows a "Greeting"
card with a single grey note "static, no pipeline" and no number badge, so turn numbering
starts at the first real message. Alternative: a backend greeting turn (prompt-owner work).

**5. Summary checks, tightened to structural evidence.**
| Check | Proposed logic (all from SSE events) |
|---|---|
| KB consulted | Of turns where a tool returned a rejection/escalation *or* `kb_retrieval` fired: N/M had ≥1 article. Drops the "agent mentioned policy" regex. |
| Responses verified | Every turn's `output_guard` verdict was `pass`. Fail text: "N responses flagged by verification". Replaces the "action-related response" regex — the output guard is the system's own unsupported-claim detector. |
| Confirmation before state change | Every `cancel_order`/`initiate_return` `tool_call` with a success result was preceded (any turn) by a `confirmation_required` result for the same tool + order_id. |
| No PII in responses | Regex on agent text for emails, phone, card, SSN patterns — excluding the demo customer's own email (decision 7). |
| Inputs screened | Every turn has an `input_guard` event (safe or blocked). |

**6. Tool-row colour mapping.** Green = success; purple = `confirmation_required` or
`requires_escalation` (system intervened); amber = ineligible / structured rejection;
red = `unhandled_error` or stream error. KB "none" = grey. Guards: green pass, purple blocked/flagged.

**7. PII vs own email.** *Recommended:* `POST /api/demo/customer` returns `email` (never
rendered); the PII check excludes it. Alternative: count it as PII (mislabels correct behaviour).

**8. CSAT field names.** *Recommended:* accept the directive's `rating`/`tags`/`free_text`
with pydantic aliases so `score`/`comment` still work; add `csat_tags JSONB` column. Tags
validated against the rating band (negative tags only for 1–3, positive only for 4–5).

**9. Optional bonus:** with the `llm.py` wrapper in place, `/chat/test` can report real
`prompt_tokens`/`completion_tokens` instead of the chars÷4 estimate. Zero behaviour change,
better eval cost numbers. Include or leave for later?

---

## Part D — Implementation plan

### Phase 1 — Backend: demo data endpoints (no agent-behaviour impact)

New `backend/routers/demo.py`, registered in `main.py`:
- `GET /api/products` → `[{id, name, category}]` ordered by name.
- `POST /api/demo/customer` → validates name (1–40 chars, letters/spaces/accents), ≤3 orders,
  product IDs exist, status ∈ {delivered, shipped, processing, cancelled, returned},
  `ordered_relative` ∈ the 5 allowed values. Creates Customer (`metadata={"demo": true,
  "created_at": iso}`, email `slug-6hex@shire.demo`), Orders with `created_at` from the relative
  date, `total_amount` from catalog price, `delivered_at = created_at + 2d` (clamped to now) for
  delivered/returned, an `OrderItem` per order, and a `Refund` row (status `processed`) for
  returned orders. Returns `{customer_id, name, email, orders:[{order_id, product_name,
  status, order_date}]}`.
- `GET /api/demo/customer/{id}` → same shape (for page reload on `/chat/:customerId`).
- `GET /api/demo/rules` → `[{text, source}]` one-liners per decision 3.
- `POST /api/conversations/{id}/end` (in `chat.py`) → `status=resolved`, `ended_at=now`;
  no-op if already resolved/escalated. Needed so CSAT accepts the rating.

Data/infra:
- `backend/tools/constants.py`: `RETURN_REVIEW_THRESHOLD_EUR = 50.0`; `order_tools.py` uses it
  (tool code, behaviour-neutral).
- Migration `006_add_csat_tags.py` + `Conversation.csat_tags` (JSONB, nullable).
- `webhooks.py`: `rating`/`tags`/`free_text` with aliases; tag enum + band validation.
- `scripts/purge_demo_customers.py --older-than-hours 24 [--dry-run]`: deletes in FK order
  (refunds → order_items → orders → audit_logs → escalations → messages → conversations →
  customers) for `metadata->>'demo' = 'true'`. Documented in `docs/tech_debt.md` as the
  future-cron note.
- Tests: `tests/test_routers/test_demo.py` (creation, validation, relative dates, delivered_at,
  refund row for returned, rules shape), update `test_webhooks.py` (tags, band validation,
  aliases), add end-conversation tests to `test_chat.py`.

### Phase 2 — Backend: turn events + cost (needs decision 2 approval)

- `backend/observability/turn_events.py`: `TurnSink`, `current_sink` ContextVar,
  `emit(event, payload)` (no-op without sink), `record_usage(response)` with
  `litellm.completion_cost` → fallback to a price table (`backend/llm_pricing.py`, shared with
  `evals/config.py` so there is one table).
- `backend/llm.py`: `acompletion(**kwargs)` wrapper (decision 2).
- One-line emits in the two guards, knowledge_service, action_service (duration measured
  around the tool handler with `time.monotonic`).
- `chat.py` stream endpoint: create sink → set ContextVar → run the graph as a task → drain
  the queue into SSE events while it runs → on completion emit `token`s (unchanged fake
  streaming), then `turn_complete {latency_ms, cost_usd, prompt_tokens, completion_tokens,
  calls}`, then `escalated`/`done`. Timeout/exception paths emit `turn_complete` with
  `error: true` so the UI can render a red step. 90 s timeout retained.
- Tests: sink unit tests; stream tests asserting event order (`input_guard` before `token`,
  `turn_complete` before `done`) with the graph mocked; confirm `/chat/test` output unchanged.
- Eval discipline: no prompt, routing, or tool-semantics change → no eval sheet needs updated
  expected values. A smoke run of one sheet after Phase 2 confirms the import swap is inert.

### Phase 3 — Frontend scaffolding

- Deps per decision 1; `vite.config.js` alias; Tailwind theme tokens for the palette
  (`#1D9E75` green, `#7F77DD` purple, `#EEEDFE`/`#3C3489` user bubble, status colours,
  returned = muted purple `#8B7FB8`).
- Routes: `/` → SetupPage, `/chat/:customerId` → ChatPage.
- `src/lib/api.js` (all fetches), `src/lib/characters.js` (15-name pool, seeded shuffle per
  load, preset generation 1–3 orders), `src/lib/statusColors.js`, `src/lib/checks.js`
  (summary checks, pure functions, unit-testable), `src/lib/pii.js`, `src/lib/format.js`.

### Phase 4 — Setup page

`pages/SetupPage.jsx` + `components/setup/{PresetCard, NameButtons, OrderRow, OrderBuilder}.jsx`.
Presets: 3 random names, random products/statuses, one click → POST demo customer → navigate.
Build your own: 2 remaining names, 1–3 order rows with three selects, "Start chatting" gated.
Loading + error states (backend down, 422).

### Phase 5 — Chat page and timeline

- `hooks/useConversation.js`: the state machine — `turns[]` each holding `{index, userMessage,
  steps[], latencyMs, response}`, running totals, `phase` (chatting | ended | csat_done),
  escalation flag. `hooks/useSSE.js` rewritten to dispatch typed events into the current turn.
- `components/chat/{TopBar, MessageList, MessageBubble, ChatInput}.jsx`.
- `components/behind/{BusinessRules, Timeline, TurnCard, PipelineStep}.jsx` with the colour
  mapping from decision 6; greeting card per decision 4; independent scroll.
- Escalation: `escalated` event → banner, input closed, phase → ended → CSAT.
- "End chat" → `POST /api/conversations/{id}/end` → phase ended.

### Phase 6 — CSAT and summary

- `components/chat/CsatFlow.jsx`: stars → tag band → optional text → submit → collapsed
  confirmation line.
- `components/chat/SummaryCard.jsx`: three metric tiles + five checks (decision 5) + restart
  buttons. "New chat as {name}" → new conversation, reset accumulators, keep customer.
  "New scenario" → `/`.

### Phase 7 — Polish and verification

- `npm run build`, serve through FastAPI, drive both pages in the browser: preset flow,
  build-your-own, a cancel with confirmation gate, an off-topic block, an escalation, CSAT,
  summary checks, restart both ways. Fix what the walkthrough surfaces.
- Run `pytest`; run one eval sheet as a smoke check.
- Update ARCHITECTURE.md frontend section (two routes, demo customer lifecycle, SSE event
  table); add the purge-cron note; log `policies.yaml` follow-up in PENDING_CHANGES.

### Files summary

**New:** `backend/routers/demo.py`, `backend/observability/turn_events.py`, `backend/llm.py`,
`backend/llm_pricing.py`, migration 006, `scripts/purge_demo_customers.py`,
`tests/test_routers/test_demo.py`, `tests/test_observability/test_turn_events.py`, ~20 frontend
files under `src/pages`, `src/components`, `src/hooks`, `src/lib`, `src/components/ui`.

**Modified:** `backend/main.py`, `backend/routers/chat.py`, `backend/routers/webhooks.py`,
`backend/db/models.py`, `backend/tools/constants.py`, `backend/tools/order_tools.py` (constant
only), `backend/guardrails/input_guard.py`, `backend/guardrails/output_guard.py`,
`backend/agents/knowledge_service.py`, `backend/agents/action_service.py`,
`backend/agents/conversation.py` (import swap only), `evals/config.py` (import price table),
`tests/test_routers/test_chat.py`, `tests/test_routers/test_webhooks.py`, `frontend/*` config,
`ARCHITECTURE.md`, `PENDING_CHANGES.md`, `docs/tech_debt.md`.

**Untouched:** `prompts/`, graph wiring, routing/loop logic, eligibility helpers, mock tools,
eval runner.
