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

### LangSmith tracing — COMPLETE (2026-03-19)

Pulled forward from Phase 5. Wired up before building the frontend so traces are visible during manual testing.

- `backend/tracing/setup.py` — only file that touches LangSmith; `init_tracing()` reads `langchain_tracing_v2`, `langchain_api_key`, `langchain_project` from settings and exports them to `os.environ` so LangGraph's built-in tracing activates automatically
- `backend/main.py` — calls `init_tracing()` once at startup
- `backend/config.py` — added `langchain_api_key` and `langchain_project` fields
- API key in `.env` (`LANGCHAIN_API_KEY`, `LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_PROJECT=ai-support-agent`)
- No LangSmith imports outside `backend/tracing/setup.py`

### Phase 4 (frontend) — NEXT

Pulling Phase 4 forward before Phase 3. Want a working chat UI to manually test the knowledge agent end-to-end before building out the full agent routing.

**To build:**
- React + Vite + shadcn/ui project setup in `frontend/`
- `Chat.jsx` — customer chat interface: message list, text input, SSE streaming via `useSSE.js` hook
- `useSSE.js` — connects to `GET /api/chat/stream/{id}`, streams tokens into the message being composed
- `CSATWidget.jsx` — star rating shown when conversation status is `resolved`
- Build output served as static files from FastAPI (`frontend/dist/` → already wired in `backend/main.py`)

### Phase 3 (full agent routing) — after frontend

Real supervisor intent classification, action agent with tool registry (track/cancel/refund), escalation handler, customer context loading, input/output guardrails.
