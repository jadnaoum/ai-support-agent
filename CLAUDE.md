# AI Customer Support Agent

Read BUILD_SPEC.md for the full build specification.
Follow the build sequence in BUILD_SPEC.md phase by phase.

After completing a task, update this file with what was implemented and note any deviations from BUILD_SPEC.md.
---

## Build progress

### Phase 1: Foundation — COMPLETE (2026-03-18)

All files pass syntax checks. Phase 1 deliverables:

- `backend/main.py` — FastAPI entry point, mounts routers, `/health` endpoint, SPA static file serving
- `backend/config.py` — pydantic-settings, reads from `.env`
- `backend/db/models.py` — all 10 SQLAlchemy models matching schema in BUILD_SPEC.md
- `backend/db/session.py` — async engine (FastAPI) + sync engine (Alembic)
- `backend/routers/chat.py` — `POST /api/conversations`, `POST /api/chat`, `GET /api/chat/stream/{id}` (stream is 501 stub until Phase 2)
- `backend/routers/admin.py` — `GET /api/conversations` (filters: status, customer, csat), `GET /api/conversations/{id}`, `GET /api/metrics`
- `backend/routers/webhooks.py` — `POST /api/csat`
- `backend/db/migrations/versions/001_initial_schema.py` — full Alembic migration: pgvector extension, all tables, all indexes
- `backend/db/seed.py` — 5 customers, 10 products, 12 orders, refunds, 5 demo conversations (all routing paths represented)
- `alembic.ini`, `backend/db/migrations/env.py` — Alembic wired to settings
- `.env.example`, `.gitignore`, `railway.toml`

**Tests backfilled and passing (2026-03-18):** 84 tests across 6 files — all green. Uses a separate `support_agent_test` DB with per-test table truncation.
- `tests/conftest.py` — sync `setup_database` fixture (uses `asyncio.run()` to avoid cross-loop conflicts), fresh `NullPool` engine per test in `db` fixture, `client` fixture, 6 data factories
- `tests/test_db/test_models.py` — constraints, FKs, JSONB, cascade delete
- `tests/test_db/test_seed.py` — counts, data integrity, idempotency
- `tests/test_routers/test_chat.py` — chat endpoints + health check
- `tests/test_routers/test_admin.py` — list/filter/detail/metrics
- `tests/test_routers/test_webhooks.py` — CSAT happy path + all error cases
- `pytest.ini` — `asyncio_mode = auto`, `asyncio_default_fixture_loop_scope = function`

**Test isolation notes:** Python 3.9 + pytest-asyncio 0.24 requires `asyncio_default_fixture_loop_scope = function` (not `session`) so each test owns its full loop lifecycle and teardown doesn't cross loop boundaries. Schema setup uses a sync session fixture with `asyncio.run()` to stay completely outside pytest-asyncio's loop management. Each test gets a fresh `create_async_engine(NullPool)` — no shared connection state.

**To run locally:**
```bash
cp .env.example .env              # fill in DB URL and API keys
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
createdb support_agent
createdb support_agent_test
alembic upgrade head              # run this first — everything depends on the schema
python -m backend.db.seed
uvicorn backend.main:app --reload
pytest                            # requires support_agent_test DB
```

Note: use `pip3` or `python3 -m pip` if `pip` is not found (macOS default).

**GitHub:** https://github.com/jadnaoum/ai-support-agent — committed and pushed 2026-03-18. `.env` excluded via `.gitignore`.

### Phase 2: Knowledge agent — COMPLETE (2026-03-19)

**All steps complete. 120 tests passing.**

**Deliverables:**

- `backend/ingestion/chunker.py` — token-based chunker using tiktoken `cl100k_base`; 300–500 token target with 50-token paragraph overlap; handles empty/oversized edge cases
- `backend/ingestion/ingest.py` — reads `docs/kb/*.md`, chunks, batch-embeds via `litellm.aembedding(text-embedding-3-small)`, upserts to `kb_documents` + `kb_chunks`, creates HNSW index after bulk load; idempotent (re-ingestion deletes old chunks first)
- `docs/kb/` — 6 demo KB documents (27.5 KB total, ~4,700 words): `returns_and_refunds.md`, `shipping.md`, `payments.md`, `account_management.md`, `warranties.md`, `faq.md`
- KB ingested: **19 chunks across 6 documents**, HNSW index live in pgvector
- `backend/agents/state.py` — `AgentState` TypedDict
- `backend/agents/knowledge_agent.py` — pgvector cosine similarity search (top-k chunks) + LiteLLM response generation
- `backend/agents/supervisor.py` — hardcoded routing to knowledge agent (Phase 2); real intent classification in Phase 3
- `backend/agents/graph.py` — LangGraph compiled graph: START → supervisor → knowledge_agent → END
- `backend/routers/chat.py` — SSE streaming endpoint live (`GET /api/chat/stream/{id}`); lazy-imports `graph` inside handler to avoid LangGraph `compile()` conflicting with pytest-asyncio's function-scoped event loops

**Tests: 120 passing** (across 10 files)
- `tests/test_ingestion/test_chunker.py` — 9 pure unit tests
- `tests/test_ingestion/test_ingest.py` — 8 integration tests (mocked embeddings)
- `tests/test_agents/test_knowledge_agent.py` — 7 tests
- `tests/test_agents/test_graph.py` — 7 tests
- `tests/test_routers/test_chat.py` — 18 tests including 7 SSE streaming tests
- `tests/conftest.py` — added `reset_sse_starlette_app_status` autouse fixture; sse_starlette stores `AppStatus.should_exit_event` as a class-level `anyio.Event` bound to the first event loop — stale on subsequent pytest-asyncio function-scoped loops, causing "Future attached to a different loop"; reset to `None` before each test forces fresh creation

**Evals: skipped for now.** Will add `evals/datasets/knowledge_qa.json` and eval runner after Phase 3.

### LangSmith tracing — COMPLETE (2026-03-20)

Pulled forward from Phase 5. Wired up before building the frontend so traces are visible during manual testing.

- `backend/tracing/setup.py` — only file that touches LangSmith; `init_tracing()` reads `langchain_tracing_v2`, `langchain_api_key`, `langchain_project`, `langchain_endpoint` from settings and exports them to `os.environ` so LangGraph's built-in tracing activates automatically
- `backend/main.py` — calls `init_tracing()` once at startup
- `backend/config.py` — added `langchain_api_key`, `langchain_project`, `langchain_endpoint` fields
- API key in `.env` (`LANGCHAIN_API_KEY`, `LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_PROJECT=ai-support-agent`, `LANGCHAIN_ENDPOINT=https://eu.api.smith.langchain.com`)
- No LangSmith imports outside `backend/tracing/setup.py`

**EU region fix (2026-03-20):** LangSmith account is on the EU region. All keys were returning 403 because requests hit the default US endpoint (`api.smith.langchain.com`). Fixed by adding `LANGCHAIN_ENDPOINT=https://eu.api.smith.langchain.com` to `.env` and exporting it in `tracing/setup.py`. Traces confirmed working.

### Minimal chat UI — COMPLETE (2026-03-19)

Pulled Phase 4 forward (minimal scope only) to enable interactive manual testing before Phase 3.

- `frontend/` — Vite + React + Tailwind project
- `frontend/src/App.jsx` — customer selector dropdown, message list with role-based styling (blue bubbles for customer, white cards for agent), text input + send button, SSE streaming, auto-scroll
- `frontend/src/hooks/useSSE.js` — thin wrapper over `EventSource`; handles `token`/`done`/`error` events, appends tokens into the last message in state
- `frontend/vite.config.js` — dev proxy: `/api` → `localhost:8000`
- `backend/routers/admin.py` — added `GET /api/customers` for the dropdown
- Build: `cd frontend && npm run build` → `dist/` served by FastAPI at `/`

**To run for manual testing:**
```bash
# Terminal 1 — backend
source .venv/bin/activate
uvicorn backend.main:app --reload

# Terminal 2 — frontend with hot reload
cd frontend && npm run dev
# open http://localhost:5173
```

**Not yet built (full Phase 4 scope for later):** typing indicator, CSAT widget, admin dashboard, polish.

**Manual testing (2026-03-20):** ANTHROPIC_API_KEY set, LangSmith EU endpoint fixed. System end-to-end tested — chat UI working, SSE streaming working, traces appearing in LangSmith.

### Phase 3: Full agent system — COMPLETE (2026-03-20)

Architecture revised from original supervisor-routing design. No separate supervisor node. The conversation agent is the only customer-facing node — it classifies intent, calls knowledge and action services, and generates all customer responses. Services return raw data only.

**Steps 1+2: COMPLETE (2026-03-20) — 128 tests passing**

- `backend/agents/conversation.py` — central customer-facing agent. Two-pass design: pass 1 classifies intent via LLM (JSON response) and sets `pending_service`; pass 2 (triggered when `actions_taken` is populated) generates the customer-facing response using service results. Handles knowledge_query, action_request, escalation_request, general intents.
- `backend/agents/knowledge_service.py` — non-customer-facing. Embeds query, runs pgvector search, returns raw chunks to conversation agent. No LLM call.
- `backend/agents/graph.py` — updated graph: `START → conversation_agent ↔ knowledge_service → END`. Conversation agent and knowledge_service form a cycle that terminates when `pending_service` clears.
- `backend/agents/state.py` — updated to Phase 3 schema: `retrieved_context`, `action_results`, `escalation_reason`, `pending_service`.
- `backend/routers/chat.py` — updated initial state fields; stream handler accumulates updates from all nodes; `agent_type="conversation"`.
- Deleted: `supervisor.py`, `knowledge_agent.py` (replaced).
- Tests: `test_conversation_agent.py` (10 tests), `test_knowledge_service.py` (7 tests), updated `test_graph.py` + `test_chat.py`.

**Implementation notes:**
- LangGraph 0.2.60 on Python 3.9 silently drops `None` state updates — use `""` as the "no pending service" sentinel instead of `None`.
- Pass 2 detection uses `bool(actions_taken)` not `bool(retrieved_context)` — knowledge_service always appends to `actions_taken` even when it finds zero chunks, so this reliably signals that a service has run.
- AsyncMock `side_effect=[list]` raises `StopIteration` when exhausted, which Python 3.9 converts to `StopAsyncIteration` inside LangGraph's async generators → `RuntimeError`. Fix: use a dispatch coroutine as `side_effect` instead of a list.

**Steps 3–5: COMPLETE (2026-03-20) — 177 tests passing**

3. `backend/agents/action_service.py` + `backend/tools/registry.py`, `backend/tools/order_tools.py`, `backend/tools/customer_tools.py` — tool registry (`ToolDefinition` dataclass + `TOOL_REGISTRY` dict) with `track_order`, `cancel_order`, `process_refund`. Action service strips null params before dispatch. Tests: `test_action_service.py` (10 tests), `test_order_tools.py` (16 tests).
   **Security refactor (2026-03-20):** `get_order_history` and `get_customer_context` removed from `TOOL_REGISTRY` — they are NOT agent-callable. Customer context and order history are loaded by `chat.py` before the graph runs and injected as read-only state. Keeping them out of the registry prevents prompt injection attacks from tricking the agent into querying arbitrary customer data. Functions remain in `order_tools.py` / `customer_tools.py` for API-layer use only.

4. `backend/agents/escalation.py` — escalation handler node. Logs to `escalations` table, marks conversation `"escalated"`, returns template handoff message keyed by reason (`customer_requested`, `low_confidence`, `repeated_failure`, `policy_exception`). Reads `conversation_id` from `config["configurable"]`; safe when absent. Tests: `test_escalation.py` (10 tests).

5. Customer context loading — `backend/tools/customer_tools.py`: `get_customer_context` (customer profile + up to 5 recent orders + risk score) and `get_risk_score` (0.0–1.0 based on refund frequency, refund ratio, escalated conversation count). Context loaded in `chat.py` before graph invocation (best-effort, never blocks on failure). `conversation.py` `_build_context_section` now includes customer name, risk score, and 3 most recent orders in the LLM prompt. Tests: `test_customer_tools.py` (13 tests).

**Step 6: COMPLETE (2026-03-20) — 203 tests passing**

6. `backend/guardrails/input_guard.py` — two-stage input guard: fast regex pattern check for obvious injection (no LLM call), then LLM classifier for subtle prompt injection, abusive, and off-topic content. Fails open on LLM error so it never blocks legitimate traffic.
   `backend/guardrails/output_guard.py` — rule-based output guard (no LLM call): impossible promise detection (catches past-tense action claims when the tool was never called) + order ID hallucination detection (catches UUIDs in the response not present in retrieved context, action results, or customer messages). Both guards wired into `conversation_agent_node`: input guard runs at the start of pass 1; output guard runs before returning the final response in pass 1 (general intent) and pass 2.
   Tests: `test_input_guard.py` (11 tests), `test_output_guard.py` (15 tests).

**Phase 3 COMPLETE.**

**Post-Phase 3 housekeeping (2026-03-20):**
- Security refactor: `get_order_history` and `get_customer_context` removed from `TOOL_REGISTRY`. Only `track_order`, `cancel_order`, `process_refund` are agent-callable. Context functions remain in source for API-layer use only.
- `SYSTEM_REFERENCE.md` generated from actual code: all API endpoints, AgentState schema, LangGraph nodes, tool registry, all 10 DB tables + 12 indexes, both guardrails, KB pipeline, config, integration points.
- `architecture.html` generated: full visual diagram of all system zones, execution paths, and KB ingestion pipeline. Open in browser.

**Post-Phase 3 policy consistency refactor (2026-03-25) — 216 tests passing:**

Schema additions (migration 002):
- `Product.final_sale` (Boolean, default False) — marks items ineligible for returns
- `Order.delivered_at` (nullable TIMESTAMPTZ) — used as anchor for return window checks
- Seed updated: `phone_case` and `usb_hub` marked `final_sale=True`; `delivered_at` set to `created_at + 5 days` for delivered/cancelled/refunded orders; electronics already had `return_window_days=14`

`cancel_order`:
- Shipped orders now blocked: "This order has already shipped and cannot be cancelled. You can return it for a refund once it arrives."
- Only `placed` status is cancellable

`process_refund` — five ordered checks added:
1. Final sale rejection (any product with `final_sale=True`)
2. Non-returnable category rejection: `gift_cards`, `digital`, `personalized`, `perishable`, `hazardous`
3. Return window check: uses `delivered_at` (fallback `updated_at`), respects per-product `return_window_days` — **defective reason bypasses this entirely** (KB policy)
4+5. `risk_score > 0.7` OR `refund_amount > 50` → `status="pending_review"` with review message; else `status="approved"`. Response dict always includes `status` field.

`action_service`: injects `risk_score` from `state["customer_context"]["risk_score"]` into `process_refund` params after null-stripping. LLM cannot supply or override it.

`conversation_agent` Pass 2: escalates with `policy_exception` when any action result has `status="pending_review"`.

`docs/kb/faq.md`: fixed cancellation contradiction — now consistent with tool (only pre-shipment cancellation possible).

**BUILD_SPEC.md and eval framework additions (2026-03-25):**
- `BUILD_SPEC.md` updated to reflect policy refactor: `final_sale` added to products schema, `delivered_at` added to orders schema, non-returnable category enum added, tool registry descriptions tightened with full eligibility rules, `risk_score` security note (injected by service layer — never LLM-provided).
- Eval framework section added to `BUILD_SPEC.md`: Phase 5 (eval runner) fully specced — `POST /api/chat/test` endpoint (gated by `APP_ENV=test`), three judge types (exact match, LLM-as-judge, rule-based), three eval categories: classification (75 cases), behavioral (80 cases), safety/robustness (60 cases). Results written to `evals/results/`. New implementation rules section added to spec.
- `eval_test_cases.xlsx` committed: 11-sheet, 215-case eval dataset covering input guard, intent classifier, output guard, KB retrieval, action execution, escalation, conversation quality, PII/data leakage, policy compliance, graceful failure, and context retention.
- `architecture_plain.html` committed: alternative plain-style architecture diagram.

### Phase 5: Eval framework — COMPLETE (2026-03-26)

**Steps 25–30 complete. 216 unit tests still passing.**

**Step 25: `POST /api/chat/test`** — gated by `APP_ENV=test` (returns 404 otherwise). Two modes:
- **Full agent run**: accepts `messages` + `mock_context` (injected as `customer_context`), runs `graph.ainvoke()`, returns `response`, `actions_taken`, `inferred_intent`, `confidence`, `requires_escalation`, `escalation_reason`, `input_guard_blocked`, `input_guard_reason`
- **Output guard test mode** (`test_output_guard=true`): accepts `agent_response`, `tools_called`, `known_ids` — runs `check_output()` only, returns `output_guard_verdict` + `output_guard_failure_type`
- `config.py`: added `app_env: str = "development"` setting
- `inferred_intent` derived from `actions_taken`: `knowledge_service` → `knowledge_query`, `action_service` → `action_request`, `requires_escalation` → `escalation_request`, else `general`
- Escalation handler already skips DB writes when `conversation_id=""` — safe for test mode
- Token counts returned: `prompt_tokens` (estimated via chars÷4+700), `completion_tokens` (estimated via chars÷4+50)

**Steps 26–30: Eval runner + judges** in `evals/`
- `evals/config.py` — judge model config, agent endpoint, sheet names, per-sheet call profiles, `MODEL_PRICE_PER_TOKEN` table for cost estimation
- `evals/judges/classification.py` — programmatic Input Guard + Intent Classifier judges; LLM fallback for Output Guard ambiguous cases (Haiku)
- `evals/judges/behavioral.py` — Sonnet rubric judge for KB Retrieval, Action Execution, Escalation, Conversation Quality
- `evals/judges/safety.py` — Sonnet rubric judge for PII, Policy Compliance, Graceful Failure, Context Retention
- `evals/run_evals.py` — CLI runner: pre-run cost estimate + y/N confirmation; reads xlsx; calls agent; judges; writes 3 result columns per run back to test sheets; updates Run History; costs tracked per sheet and per run
- `evals/requirements.txt` — openpyxl, requests, litellm
- `evals/eval_test_cases.xlsx` — 11-sheet, 215-case eval dataset (file renamed from `eval_data.xlsx` per spec)

**To run evals:**
```bash
export APP_ENV=test
uvicorn backend.main:app --reload   # terminal 1 — agent with test endpoint
python evals/run_evals.py --tag "v1.0_baseline" --desc "Full baseline run"   # terminal 2 — shows cost estimate, prompts y/N
python evals/run_evals.py --tag "v1.1" --desc "intent fix" --sheets "Input Guard,Intent Classifier"
```

**Step 31 (calibration): skipped** — manual one-time run; do after full baseline is established.

**v1.0_baseline results (2026-03-27) — $1.14 actual cost:**

| Sheet | Pass% |
|---|---|
| Input Guard | 92% |
| Conversation Quality | 83% |
| PII & Data Leakage | 67% |
| Intent Classifier | 44% |
| Output Guard | 44% |
| Escalation | 43% |
| Policy Compliance | 33% |
| Context Retention | 33% |
| KB Retrieval | 25% |
| Graceful Failure | 23% |
| Action Execution | 20% |
| **OVERALL** | **46%** |

**Deviations from BUILD_SPEC.md:**

1. **Eval data filename**: spec says `eval_data.xlsx`; implemented as `eval_test_cases.xlsx`.
2. **No separate results file**: spec says write a detailed `evals/results/{tag}.xlsx` with full judge reasoning and a Regressions sheet. Not implemented — all results are written back into the main `eval_test_cases.xlsx` as added columns. The Regressions sheet is also not implemented.
3. **Run History format**: spec defines a wide format (one column per eval type per row). Changed to long format at user request — one row per eval type per run, plus an OVERALL row. Columns: `run_id | date | version_tag | change_description | eval_type | pass% | total_tokens | total_cost_usd | judge_model | notes`.
4. **Per-sheet run columns**: spec says one column per run (verdict only, color-coded PASS/PARTIAL/FAIL). Implemented as four columns per run: `{tag} ($X.XX)` verdict, `{tag} response`, `{tag} reasoning`, `{tag} failure_reason`.
5. **Cost tracking**: not in spec — added at user request. Pre-run estimate (with y/N confirmation before any spend) + actual per-call cost tracking using LiteLLM response metadata. Cost shown per sheet in Run History.
6. **`judge_model` field**: spec says store the model string or "tiered". Changed at user request to a descriptive string: `"classification: claude-haiku-4-5-20251001 | behavioral+safety: claude-sonnet-4-6"`.
7. **`needs_clarification` intent**: now implemented in the agent (see post-Phase 5 changes below).
8. **Agent URL config**: runner reads `EVAL_AGENT_URL` env var (default `http://localhost:8000`). Must be exported before piping input — e.g. `export EVAL_AGENT_URL=http://localhost:8001 && python evals/run_evals.py ...`. Do not use `VAR=val printf 'y' | python ...` pattern — the env var only applies to `printf`, not to `python`.
9. **Binary Pass/Fail scoring**: spec originally included `partial` as a third verdict. All judges now use binary Pass/Fail only — `partial` is normalised to `fail` in the runner. Spreadsheet rubrics and color coding updated accordingly.

### Post-Phase 5 changes (2026-03-28) — 216 tests still passing

**Eval runner and spreadsheet cleanup:**
- `evals/run_evals.py`: fixed role normalisation bug (`user`/`human` → `customer`, `assistant`/`bot` → `agent`); added `test_id` + `version_tag` params passed to agent on every call for LangSmith traceability; fixed classification sheet response columns (Input Guard shows `"blocked: <reason>"` or `"passed: safe"`, Intent Classifier shows `inferred_intent`, Output Guard shows `"<verdict>: <failure_type>"`); updated sheet layout to row 1 = merged group label, row 2 = headers, row 3+ = data; extended run groups from 3 to 4 columns (added `failure_reason` column)
- `eval_test_cases.xlsx`: Run History cleaned to v1.0_baseline only (removed broken runs); all 11 test sheets trimmed to latest run group; row 1 merged group labels added; judge rubrics rewritten to binary Pass/Fail with per-sheet `failure_reason` enums
- `backend/routers/chat.py`: added `test_id` and `version_tag` to `TestChatRequest`; LangGraph `ainvoke` config now passes them as `tags` and `metadata` so every eval trace is linked to its test case and run in LangSmith

**`needs_clarification` intent (items #5 and #6 from pending changes):**
- `backend/agents/state.py`: added `last_turn_was_clarification: bool` — True when the previous agent turn was a clarifying question; reset to False on all other turns
- `backend/agents/conversation.py`: `INTENT_PROMPT` updated with 5th intent and usage guidance (when to use / when not to); `_classify_intent` extracts `clarification_prompt` from LLM response; `conversation_agent_node` Pass 1 handler:
  - `needs_clarification` + `last_turn_was_clarification=False` → return clarifying question directly, set flag True
  - `needs_clarification` + `last_turn_was_clarification=True` → escalate with `"unable_to_clarify"` (never ask twice in a row)
  - Output guard blocks the clarifying question → escalate with `"unable_to_clarify"`
- `backend/agents/escalation.py`: added `"unable_to_clarify"` to `_HANDOFF_MESSAGES` with a specific customer-facing message
- `backend/routers/chat.py`: both initial state dicts (SSE handler + test endpoint) initialise `last_turn_was_clarification: False`
- `BUILD_SPEC.md` updated: `last_turn_was_clarification` added to AgentState schema; `conversation_agent` node description updated; `unable_to_clarify` added to escalations reason enum; eval framework updated with binary Pass/Fail scoring, `failure_reason` enums per sheet, updated judge JSON example, and updated per-sheet run column description

### Post-Phase 5 changes continued (2026-03-28) — 216 tests still passing

**`needs_clarification` fallback — escalate instead of general:**
- `backend/agents/conversation.py`: when intent is `needs_clarification` and `last_turn_was_clarification` is already True, route to escalation with reason `"unable_to_clarify"` instead of falling through to general. Output guard blocking the clarifying question also routes to `"unable_to_clarify"`.
- `backend/agents/escalation.py`: added `"unable_to_clarify"` entry to `_HANDOFF_MESSAGES`

**KB Retrieval judge — use actual KB content as reference:**
- `evals/judges/behavioral.py`: replaced `available_kb_articles` + `expected_article` fields in `_KB_PROMPT` with `reference_content` (actual fetched chunk text). Judge now compares agent response against real KB text instead of inferring from its own knowledge. When `reference_content` is null, judge verifies the agent didn't fabricate.
- `evals/run_evals.py`: added `_fetch_kb_reference_content(titles)` async function — deterministic title lookup against `kb_chunks JOIN kb_documents` via `AsyncSessionLocal`; injected as `reference_content` into test case before judge call
- `eval_test_cases.xlsx` KB Retrieval sheet: renamed `expected_article` → `reference_articles`; values converted to JSON arrays mapped to actual DB titles (e.g. `["Returns and Refunds Policy"]`); KB-008, KB-011, KB-014, KB-015, KB-018, KB-019 set to `[]` (no real article exists)

**New Policy Compliance test case PC-016:**
- `eval_test_cases.xlsx` Policy Compliance sheet: customer states delivery date and asks for specific return deadline. Agent must retrieve 30-day standard window from KB, compute March 10 + 30 days = April 9 2026, and state that specific date. Vague answer without the computed date = Fail with `incomplete_answer`.
- `incomplete_answer` added to the `failure_reason` enum in all 15 existing Policy Compliance rubrics

**Conversation Quality rubrics — both tone and substance required:**
- `eval_test_cases.xlsx` Conversation Quality sheet: all 15 rubrics rewritten. Pass now explicitly requires correct tone AND correct behavioral substance — both required. Fail path A: substance right, tone wrong (maps to `robotic`, `dismissive`, `over_enthusiastic`, `tone_mismatch`). Fail path B: tone right, substance wrong (maps to `ignored_context`). All `Partial` language removed.

**Output Guard scoring fix:**
- `evals/judges/classification.py`: removed `partial` verdict entirely (was 0.5 credit). Judge now binary pass/fail. Wrong verdict direction → `fail` with `failure_reason: "wrong_verdict"`. Correct verdict but wrong `failure_type` → `fail` with `failure_reason: "wrong_failure_type"` (previously triggered an LLM call to decide between partial/fail — now deterministic). Input Guard wrong block reason also changed from `partial` to `fail` with `failure_reason: "wrong_block_reason"`. Removed now-unused `litellm`, `json`, and `JUDGE_MODEL_CLASSIFICATION` imports. `_verdict` now returns `failure_reason` field.

**GF-005 rubric update:**
- `eval_test_cases.xlsx` Graceful Failure sheet: GF-005 Fail criteria expanded — previously only "declares the order doesn't exist definitively"; now also covers "fabricates a reason for the lookup failure (e.g., claims invalid order ID format)" and "doesn't acknowledge the possibility of a typo"

**`context_summary` surfaced for escalation evals:**
- `backend/agents/state.py`: added `context_summary: str` field
- `backend/agents/escalation.py`: `escalation_handler_node` now returns `context_summary` in its state update (was previously only written to DB and discarded)
- `backend/routers/chat.py`: `TestChatResponse` now includes `context_summary: str = ""`; populated from `final_state.get("context_summary", "")`; initialized to `""` in both initial state dicts
- `evals/judges/behavioral.py`: escalation judge prompt now receives `context_summary` and evaluates it: did the agent capture what the customer needed and why escalation was triggered in a way that's useful for the human agent?
- `evals/run_evals.py`: added `_SHEET_EXTRA_COLS = {"Escalation": [("escalation_summary", "context_summary")]}` and generalised `_append_run_column` with `extra_cols` parameter — Escalation sheet now gets a 5th column per run (`{tag} escalation_summary`)

**BUILD_SPEC.md updates:**
- `unable_to_clarify` added to escalations reason enum
- `last_turn_was_clarification` and `context_summary` added to AgentState schema
- `conversation_agent` node description updated with clarification capping logic
- Eval framework section updated: binary Pass/Fail scoring, `failure_reason` enums per sheet, updated judge JSON example, updated per-sheet run column description, KB Retrieval reference content approach documented

**Next: Phase 4 — Frontend**
- Typing indicator while agent streams
- CSAT widget shown when conversation resolves
- Admin dashboard (`Admin.jsx`) — conversation log with filters, metrics panel
- Audit log viewer
