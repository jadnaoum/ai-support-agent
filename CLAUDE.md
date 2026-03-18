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

### Phase 2: Knowledge agent — IN PROGRESS (2026-03-18)

**Steps 5–7 complete.** Steps 8–10 remaining.

**Completed deliverables:**

- `backend/ingestion/chunker.py` — token-based chunker using tiktoken `cl100k_base`; 300–500 token target with 50-token paragraph overlap; handles empty/oversized edge cases
- `backend/ingestion/ingest.py` — reads `docs/kb/*.md`, chunks, batch-embeds via `litellm.aembedding(text-embedding-3-small)`, upserts to `kb_documents` + `kb_chunks`, creates HNSW index after bulk load; idempotent (re-ingestion deletes old chunks first)
- `docs/kb/` — 6 demo KB documents (27.5 KB total, ~4,700 words): `returns_and_refunds.md`, `shipping.md`, `payments.md`, `account_management.md`, `warranties.md`, `faq.md`
- `backend/db/models.py` — added `embedding = Column(Vector(1536), nullable=True)` to `KBChunk`
- `backend/requirements.txt` — added `tiktoken>=0.7.0`
- KB ingested: **19 chunks across 6 documents**, HNSW index live in pgvector

**Tests: 101 passing** (84 Phase 1 + 9 chunker + 8 ingest)
- `tests/test_ingestion/test_chunker.py` — 9 pure unit tests for chunker (no DB, no mocks)
- `tests/test_ingestion/test_ingest.py` — 8 integration tests mocking `litellm.aembedding`

**Remaining steps:**
8. Knowledge agent: pgvector search + response generation (`backend/agents/knowledge_agent.py`)
9. Basic LangGraph: supervisor (hardcoded to knowledge agent) → knowledge agent → response (`backend/agents/graph.py`, `supervisor.py`)
10. SSE streaming endpoint (wire up `GET /api/chat/stream/{id}`)
- Tests for agents and streaming
- Evals: `evals/eval_retrieval.py` + `evals/datasets/knowledge_qa.json`

**Implementation plan:** `phase2_plan.txt` in project root.
