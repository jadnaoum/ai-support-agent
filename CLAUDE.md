# AI Customer Support Agent

Read BUILD_SPEC.md for the full build specification.
Read ARCHITECTURE.md for architecture decisions and reasoning.

Follow the build sequence in BUILD_SPEC.md phase by phase.

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

### Phase 3: Full agent system — IN PROGRESS

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

**Next: Phase 4 — Frontend**
- Typing indicator while agent streams
- CSAT widget shown when conversation resolves
- Admin dashboard (`Admin.jsx`) — conversation log with filters, metrics panel
- Audit log viewer
