# System Reference

Generated from the actual codebase. Last updated: 2026-03-20.

---

## Table of Contents

1. [API Endpoints](#api-endpoints)
2. [Agent State](#agent-state)
3. [LangGraph Architecture](#langgraph-architecture)
4. [Tool Registry](#tool-registry)
5. [Database Schema](#database-schema)
6. [Guardrails](#guardrails)
7. [KB Ingestion Pipeline](#kb-ingestion-pipeline)
8. [Configuration](#configuration)
9. [Integration Points](#integration-points)

---

## API Endpoints

### Chat (`backend/routers/chat.py`)

#### `POST /api/conversations`
Create a new conversation for a customer.

**Request body:**
```json
{ "customer_id": "uuid" }
```
**Response:**
```json
{ "conversation_id": "uuid" }
```
**Logic:** Validates customer exists; creates `Conversation` row with `status="active"`.
**Errors:** `404` if customer not found.

---

#### `POST /api/chat`
Persist a customer message.

**Request body:**
```json
{ "conversation_id": "uuid", "customer_id": "uuid", "message": "string" }
```
**Response:**
```json
{ "conversation_id": "uuid", "message_id": "uuid" }
```
**Logic:** Validates conversation exists and belongs to the customer; inserts `Message` with `role="customer"`.
**Errors:** `404` conversation not found, `403` customer mismatch.

---

#### `GET /api/chat/stream/{conversation_id}`
Run the agent and stream the response via SSE.

**Path param:** `conversation_id`
**Response:** `text/event-stream`

| Event | Data |
|---|---|
| `token` | One word (space-appended) of the agent's response |
| `done` | Empty string — stream complete |
| `error` | Exception message string |

**Logic (in order):**
1. Validate conversation exists
2. Load latest customer `Message` (role=customer, most recent)
3. Load conversation history up to `max_context_messages` (customer + agent only)
4. Load customer context via `get_customer_context()` — best-effort, failure returns `{}`
5. Build `AgentState` and invoke LangGraph in `stream_mode="updates"`
6. Accumulate state updates from all nodes; stream `response` word-by-word
7. Persist agent `Message` (`agent_type="conversation"`) and `AuditLog`
8. Yield `done` event

**Errors:** `404` conversation not found, `422` no customer message found.

---

### Admin (`backend/routers/admin.py`)

#### `GET /api/customers`
List all customers.

**Response:**
```json
[{ "id": "uuid", "name": "string", "email": "string" }]
```

---

#### `GET /api/conversations`
List conversations with optional filters.

**Query params:**

| Param | Type | Default | Description |
|---|---|---|---|
| `status` | string | — | Filter by `active`, `resolved`, or `escalated` |
| `customer_id` | string | — | Filter by customer |
| `csat_min` | int | — | Minimum CSAT score (inclusive) |
| `csat_max` | int | — | Maximum CSAT score (inclusive) |
| `limit` | int | 50 | Page size |
| `offset` | int | 0 | Page offset |

**Response:** Array of conversation summary objects with message count.

---

#### `GET /api/conversations/{conversation_id}`
Full conversation detail for admin.

**Response:** Conversation object with nested `messages`, `audit_logs`, and `escalations`.

---

#### `GET /api/metrics`
Aggregated stats.

**Response:**
```json
{
  "total_conversations": 42,
  "active": 5,
  "resolved": 30,
  "escalated": 7,
  "escalation_rate": 0.167,
  "avg_csat": 4.2,
  "csat_count": 18
}
```

---

### Webhooks (`backend/routers/webhooks.py`)

#### `POST /api/csat`
Submit a CSAT rating after a conversation.

**Request body:**
```json
{ "conversation_id": "uuid", "score": 4, "comment": "optional string" }
```
**Response:**
```json
{ "conversation_id": "uuid", "score": 4, "message": "Thank you for your feedback!" }
```
**Errors:** `404` not found, `400` conversation still active, `409` CSAT already submitted, `422` score out of range (1–5).

---

### Other

#### `GET /health`
```json
{ "status": "ok", "service": "ai-support-agent" }
```

#### `GET /docs`
FastAPI auto-generated Swagger UI.

#### `GET /` (and all non-API paths)
Serves `frontend/dist/index.html` (React SPA).

---

## Agent State

Defined in `backend/agents/state.py`.

```python
class AgentState(TypedDict):
    messages: list[dict]        # [{"role": "customer"|"agent", "content": str}, ...]
    customer_id: str            # Set by chat.py before graph runs
    customer_context: dict      # {name, email, order_count, recent_orders, risk_score}
                                # Loaded by chat.py — read-only inside the graph
    retrieved_context: list     # [{chunk_text, title, category, similarity}, ...]
                                # Written by knowledge_service
    action_results: list        # [tool output dicts, ...]
                                # Written by action_service
    confidence: float           # LLM classification confidence OR top KB similarity
    requires_escalation: bool
    escalation_reason: str      # "customer_requested" | "low_confidence" | "policy_exception"
    actions_taken: list[dict]   # Audit trail: [{service, action, ...}, ...]
    response: str               # Final customer-facing response
    pending_service: str        # Routing sentinel: "knowledge"|"action"|"escalation"|""
    pending_action: dict        # {"tool": str, "params": dict} — set by conversation_agent
                                # consumed and cleared by action_service
```

**Routing convention:** `pending_service=""` (empty string) is the "no pending work" sentinel. LangGraph 0.2.x on Python 3.9 silently drops `None` state updates, so `""` is used instead.

**Pass-2 detection:** `bool(state["actions_taken"])` — services always append to this even when results are empty, so it reliably indicates a service has already run.

---

## LangGraph Architecture

Defined in `backend/agents/graph.py`.

### Node Responsibilities

| Node | Customer-facing | LLM call | DB write |
|---|---|---|---|
| `conversation_agent` | Yes | Yes (intent + response) | No |
| `knowledge_service` | No | No (embedding only) | No |
| `action_service` | No | No | Yes (cancel/refund) |
| `escalation_handler` | No (returns message) | No | Yes (escalations table) |

### Graph Edges

```
START
  └─→ conversation_agent
        ├─→ knowledge_service ──→ conversation_agent (Pass 2)
        ├─→ action_service ─────→ conversation_agent (Pass 2)
        ├─→ escalation_handler ─→ END
        └─→ END
```

### Routing (`_route_after_conversation`)

```python
pending = state.get("pending_service", "")
"knowledge"   → "knowledge_service"
"action"      → "action_service"
"escalation"  → "escalation_handler"
""            → END
```

### Conversation Agent — Two-Pass Design

**Pass 1** (no service results yet — `actions_taken` is empty):
1. Run `check_input()` — block injection/abusive/off-topic messages
2. Call LLM with `INTENT_PROMPT` → `{intent, confidence, action?, params?}`
3. Route by intent:
   - `knowledge_query` → `{pending_service: "knowledge"}`
   - `action_request` → `{pending_service: "action", pending_action: {tool, params}}`
   - `escalation_request` → `{pending_service: "escalation", requires_escalation: True, escalation_reason: "customer_requested"}`
   - `general` → generate response directly, run output guard, `{response, pending_service: ""}`

**Pass 2** (triggered when `actions_taken` is non-empty):
1. If KB results present and `top_similarity < confidence_threshold (0.7)` → escalate with `low_confidence`
2. Build context section: customer name + risk score + recent orders + KB chunks + action results
3. Call LLM with `RESPONSE_PROMPT` + context → response string
4. Run `check_output()` — block impossible promises, hallucinated IDs
5. Return `{response, confidence, pending_service: ""}`

### Execution Config

The graph receives `config = {"configurable": {"db": AsyncSession, "conversation_id": str}}` from `chat.py`. Nodes access these via `config["configurable"].get(...)`. The module-level `graph = build_graph()` is compiled once at import time; `chat.py` lazy-imports it to avoid conflicts with pytest-asyncio's event loop management.

---

## Tool Registry

Defined in `backend/tools/registry.py`.

**Only these three tools are agent-callable.** `get_order_history` and `get_customer_context` are NOT in the registry — they are called by the API layer only (see [Security note](#security-note)).

### `track_order`
Look up order status and item details.

| Param | Type | Required | Description |
|---|---|---|---|
| `order_id` | str | No | Order to look up; defaults to most recent |

**Returns:**
```json
{
  "success": true,
  "order_id": "uuid",
  "status": "placed|shipped|delivered|cancelled|refunded",
  "total": 99.99,
  "placed_at": "2026-03-10T...",
  "items": [{ "product": "Widget", "quantity": 1, "price": 99.99 }]
}
```

---

### `cancel_order`
Cancel a placed or shipped order.

| Param | Type | Required | Description |
|---|---|---|---|
| `order_id` | str | No | Defaults to most recent |
| `reason` | str | No | Defaults to `"customer_requested"` |

**Returns:** `{success, order_id, message}` or `{success: false, error}` (if delivered/already cancelled).

---

### `process_refund`
Initiate a refund for a delivered or cancelled order.

| Param | Type | Required | Description |
|---|---|---|---|
| `order_id` | str | No | Defaults to most recent |
| `amount` | float | No | Partial refund; defaults to full order total |
| `reason` | str | No | `defective`, `changed_mind`, `wrong_item`, `late_delivery`, `other` |

**Returns:**
```json
{
  "success": true,
  "order_id": "uuid",
  "refund_id": "uuid",
  "amount": 99.99,
  "message": "Refund of $99.99 has been approved..."
}
```

**Reason normalisation:** "broken/damaged" → `defective`, "wrong" → `wrong_item`, "late" → `late_delivery`.
**Creates:** `Refund` record with `status="approved"`, `initiated_by="agent"`; sets `order.status="refunded"`.

### Security Note

`get_order_history` and `get_customer_context` are **not registered as agent-callable tools**. Customer context and order history are loaded by `chat.py` before the graph runs and injected as read-only `AgentState`. Registering them would allow prompt injection attacks to request arbitrary `customer_id` lookups.

---

## Database Schema

All tables use UUID primary keys. Timestamps are timezone-aware.

### `customers`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `name` | VARCHAR | NOT NULL |
| `email` | VARCHAR | UNIQUE, NOT NULL |
| `created_at` | TIMESTAMPTZ | server default `now()` |
| `metadata` | JSONB | nullable |

### `products`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `name` | VARCHAR | NOT NULL |
| `category` | VARCHAR | electronics/clothing/home_goods/accessories |
| `price` | NUMERIC(10,2) | NOT NULL |
| `return_window_days` | INTEGER | default 30 |
| `warranty_months` | INTEGER | nullable |
| `metadata` | JSONB | nullable |
| `created_at` | TIMESTAMPTZ | server default |

### `orders`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `customer_id` | UUID | FK → customers, indexed |
| `status` | VARCHAR | placed/shipped/delivered/cancelled/refunded |
| `total_amount` | NUMERIC(10,2) | NOT NULL |
| `created_at` | TIMESTAMPTZ | server default |
| `updated_at` | TIMESTAMPTZ | auto-updated |

### `order_items`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `order_id` | UUID | FK → orders |
| `product_id` | UUID | FK → products |
| `quantity` | INTEGER | default 1 |
| `price_at_purchase` | NUMERIC(10,2) | NOT NULL |

### `refunds`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `order_id` | UUID | FK → orders |
| `customer_id` | UUID | FK → customers, indexed |
| `amount` | NUMERIC(10,2) | NOT NULL |
| `reason` | VARCHAR | NOT NULL — defective/changed_mind/wrong_item/late_delivery/other |
| `status` | VARCHAR | requested/approved/rejected/processed |
| `initiated_by` | VARCHAR | customer/agent/system |
| `conversation_id` | UUID | FK → conversations, nullable |
| `created_at` | TIMESTAMPTZ | server default |
| `processed_at` | TIMESTAMPTZ | nullable |

### `conversations`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `customer_id` | UUID | FK → customers, indexed |
| `status` | VARCHAR | active/resolved/escalated, indexed |
| `started_at` | TIMESTAMPTZ | server default |
| `ended_at` | TIMESTAMPTZ | nullable |
| `summary` | TEXT | nullable |
| `messages_purged` | BOOLEAN | default False |
| `csat_score` | INTEGER | nullable, 1–5, indexed (partial) |
| `csat_comment` | TEXT | nullable |

### `messages`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `conversation_id` | UUID | FK → conversations, indexed |
| `role` | VARCHAR | customer/agent/system |
| `content` | TEXT | NOT NULL |
| `agent_type` | VARCHAR | nullable — conversation/knowledge_service/action_service/escalation |
| `created_at` | TIMESTAMPTZ | server default |

### `audit_logs`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `conversation_id` | UUID | FK → conversations, indexed |
| `message_id` | UUID | FK → messages, indexed, nullable |
| `agent_type` | VARCHAR | NOT NULL |
| `action` | VARCHAR | NOT NULL |
| `input_data` | JSONB | nullable |
| `output_data` | JSONB | nullable |
| `routing_decision` | VARCHAR | nullable |
| `confidence` | FLOAT | nullable |
| `created_at` | TIMESTAMPTZ | server default |

### `escalations`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `conversation_id` | UUID | FK → conversations, indexed |
| `reason` | VARCHAR | customer_requested/low_confidence/unknown_intent/policy_exception/repeated_failure, indexed |
| `agent_confidence` | FLOAT | nullable |
| `context_summary` | TEXT | nullable |
| `created_at` | TIMESTAMPTZ | server default |

### `kb_documents`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `filename` | VARCHAR | NOT NULL |
| `title` | VARCHAR | NOT NULL |
| `category` | VARCHAR | returns/shipping/payments/products/account/warranty |
| `version` | INTEGER | default 1 |
| `ingested_at` | TIMESTAMPTZ | server default |
| `metadata` | JSONB | nullable |

### `kb_chunks`
| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `document_id` | UUID | FK → kb_documents (cascade delete), indexed |
| `chunk_text` | TEXT | NOT NULL |
| `chunk_index` | INTEGER | NOT NULL |
| `embedding` | VECTOR(1536) | pgvector — HNSW index (cosine) |
| `created_at` | TIMESTAMPTZ | server default |

### Indexes (from migration)
```sql
idx_messages_conversation       ON messages(conversation_id)
idx_orders_customer             ON orders(customer_id)
idx_refunds_customer            ON refunds(customer_id)
idx_conversations_customer      ON conversations(customer_id)
idx_audit_conversation          ON audit_logs(conversation_id)
idx_audit_message               ON audit_logs(message_id)
idx_escalations_conversation    ON escalations(conversation_id)
idx_escalations_reason          ON escalations(reason)
idx_conversations_status        ON conversations(status)
idx_conversations_csat          ON conversations(csat_score) WHERE csat_score IS NOT NULL
idx_kb_embedding                ON kb_chunks USING hnsw (embedding vector_cosine_ops)
idx_kb_chunks_document          ON kb_chunks(document_id)
```

---

## Guardrails

### Input Guard (`backend/guardrails/input_guard.py`)

Runs at the **start of Pass 1** in `conversation_agent_node`. Returns early with a blocked response if triggered — LangGraph never sees the message.

**Stage 1 — Fast pattern check (no LLM):**

Regex patterns (case-insensitive) that trigger immediate rejection:
- `ignore (all|your)? (previous|prior|above)? instructions`
- `disregard ... instructions`
- `you are now`
- `act as (if you are|a )`
- `new persona`, `jailbreak`, `dan mode`, `developer mode`
- `system prompt`
- `<|im_start|>`, `<|im_end|>`, `[INST]`, `### system`

**Stage 2 — LLM classification (if Stage 1 passes):**

Categories: `safe` | `prompt_injection` | `abusive` | `off_topic`

Fail-open: any LLM error → `{safe: True}` (never silently blocks legitimate traffic).

**Return shape:**
```python
{"safe": True}
{"safe": False, "reason": str, "blocked_response": str}
```

---

### Output Guard (`backend/guardrails/output_guard.py`)

Runs **before returning the final response** in both Pass 1 (general intent) and Pass 2. No LLM call — rule-based only.

**Check 1 — Impossible promise detection:**

Patterns matched against response text:

| Pattern | Required tool in `actions_taken` |
|---|---|
| `I've cancelled` / `I have cancelled` | `cancel_order` |
| `I've processed/initiated/submitted ... refund` | `process_refund` |
| `your order has been cancelled` | `cancel_order` |
| `refund has been processed/initiated/approved` | `process_refund` |

If pattern fires but tool was not called → `{safe: False, reason: "impossible_promise"}`.

**Check 2 — Hallucinated ID detection:**

Scans response for UUID-format strings. Checks against all IDs legitimately in scope:
- `customer_context.recent_orders[].order_id`
- `action_results[]` (all string values matching UUID format)
- `retrieved_context[].chunk_text` (scanned)
- Customer messages (customer may have provided an ID)

Any UUID in the response not in the known set → `{safe: False, reason: "hallucinated_id"}`.

Both failures route to `pending_service="escalation"` with `escalation_reason="policy_exception"`.

---

## KB Ingestion Pipeline

### Chunker (`backend/ingestion/chunker.py`)

`chunk_text(text, max_tokens=400, overlap_tokens=50) → list[str]`

1. Split text on `\n\n` into paragraphs
2. Tokenize each paragraph via `tiktoken cl100k_base`
3. Accumulate paragraphs into a buffer; flush when adding a paragraph would exceed `max_tokens`
4. On flush: build overlap seed by backfilling the last `overlap_tokens` tokens from the flushed chunk; new buffer starts from overlap seed
5. Final buffer is flushed at end

Result: chunks of ~300–500 tokens with 50-token paragraph overlap.

### Ingest Script (`backend/ingestion/ingest.py`)

`ingest_all()` — run manually to populate the knowledge base:

1. Glob `docs/kb/*.md`
2. For each file:
   a. Delete existing `KBDocument` + cascaded `KBChunk` rows (idempotent)
   b. Insert new `KBDocument` (filename, title extracted from H1, category from filename map)
   c. Chunk file text
   d. Batch-embed all chunks via `litellm.aembedding("text-embedding-3-small")` → `Vector(1536)`
   e. Insert `KBChunk` rows
3. Create HNSW index on `kb_chunks.embedding` (`vector_cosine_ops`)

Current state: **19 chunks across 6 documents** — returns_and_refunds, shipping, payments, account_management, warranties, faq.

---

## Configuration

`backend/config.py` — `Settings(BaseSettings)`, populated from environment / `.env`.

| Key | Default | Description |
|---|---|---|
| `database_url` | `postgresql+asyncpg://localhost/support_agent` | Async DB URL (FastAPI / pytest) |
| `database_url_sync` | `postgresql+pg8000://localhost/support_agent` | Sync DB URL (Alembic) |
| `litellm_model` | `claude-sonnet-4-6` | Model for all LLM completions |
| `litellm_embedding_model` | `text-embedding-3-small` | Model for KB embeddings |
| `anthropic_api_key` | `""` | Set in `.env` |
| `openai_api_key` | `""` | Set in `.env` (for embeddings) |
| `confidence_threshold` | `0.7` | Min KB similarity; below this triggers escalation |
| `max_context_messages` | `50` | Message history window sent to LLM |
| `message_retention_days` | `60` | Future: message purge threshold |
| `langchain_tracing_v2` | `false` | Enable LangSmith tracing |
| `langchain_api_key` | `""` | LangSmith API key |
| `langchain_project` | `ai-support-agent` | LangSmith project name |
| `langchain_endpoint` | `https://api.smith.langchain.com` | EU: `https://eu.api.smith.langchain.com` |

Accessed via `get_settings()` (cached with `@lru_cache`).

---

## Integration Points

### LiteLLM
- **All LLM calls** go through `litellm.acompletion()` — never a provider SDK directly
- Used in: `conversation.py` (intent + response), `input_guard.py` (classification)
- Used for embeddings: `litellm.aembedding()` in `knowledge_service.py` and `ingest.py`
- Model configured via `settings.litellm_model` / `settings.litellm_embedding_model`

### LangGraph
- Compiled graph at module level in `graph.py`: `graph = build_graph()`
- `chat.py` lazy-imports `graph` inside the handler to avoid `compile()` running at pytest collection time
- Invoked via `graph.astream(state, config, stream_mode="updates")`
- State passed as `AgentState` TypedDict; config carries `{"configurable": {"db": ..., "conversation_id": ...}}`

### pgvector
- Used in `knowledge_service.py` for cosine similarity search
- Raw SQL query: `ORDER BY embedding <=> :query_embedding LIMIT 5`
- Similarity computed as `1.0 - cosine_distance`
- HNSW index created by ingestion script after bulk load

### LangSmith
- **Only imported in** `backend/tracing/setup.py`
- `init_tracing()` exports `LANGCHAIN_*` env vars; LangGraph picks them up automatically
- Called once at startup in `main.py`
- EU region: `LANGCHAIN_ENDPOINT=https://eu.api.smith.langchain.com`

### SSE (Server-Sent Events)
- Library: `sse_starlette`
- Endpoint: `GET /api/chat/stream/{conversation_id}`
- Response: `EventSourceResponse` wrapping an async generator
- Events: `token` (word), `done` (empty), `error` (exception message)
- `AppStatus.should_exit_event` reset to `None` in `conftest.py` before each test to prevent stale anyio event loop references

### PostgreSQL
- Async driver: `asyncpg` (FastAPI, tests)
- Sync driver: `pg8000` (Alembic migrations)
- Session management: `AsyncSession` from `sqlalchemy.ext.asyncio`
- Tests use `NullPool` engines, separate `support_agent_test` database, per-test table truncation
