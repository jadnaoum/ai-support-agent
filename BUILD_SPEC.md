# BUILD SPEC — AI Customer Support Agent

> This is the build specification. It tells you WHAT to build and HOW.
> For decision reasoning and alternatives considered, see ARCHITECTURE.md.

---

## Project summary

E-commerce customer support system with a single conversation agent backed by service agents. One conversation agent owns the entire customer interaction — tone, dialogue, and decision-making. It calls on a knowledge service (RAG search) and an action service (order operations) as needed, and decides when to escalate to a human. The customer only ever talks to one agent. Includes logging, CSAT collection, and an admin dashboard. Publicly hosted for demos.

---

## Tech stack

| Layer | Tool | Notes |
|---|---|---|
| Backend framework | Python + FastAPI | SSE for streaming responses, REST for everything else |
| Agent orchestration | LangGraph | State graph: conversation agent calls knowledge and action services |
| LLM abstraction | LiteLLM | Wraps all LLM calls — never call an LLM provider directly |
| Database | PostgreSQL + pgvector | Single DB for structured data AND vector search |
| Tracing | LangSmith | All tracing config isolated in one module — no LangSmith imports in agent logic |
| Frontend | React + Vite + shadcn/ui | Served as static files from FastAPI, not separately hosted |
| Hosting | Railway | Single container: FastAPI serves API + static frontend |

---

## Project structure

```
project-root/
├── backend/
│   ├── main.py                  # FastAPI app entry point, mounts routers, serves static files
│   ├── config.py                # Environment variables, LLM config, DB connection
│   ├── routers/
│   │   ├── chat.py              # POST /api/chat (send message), GET /api/chat/stream (SSE)
│   │   ├── admin.py             # GET /api/conversations, GET /api/metrics, GET /api/csat
│   │   └── webhooks.py          # POST /api/csat (collect rating), future integrations
│   ├── agents/
│   │   ├── graph.py             # LangGraph state machine definition
│   │   ├── conversation.py      # Conversation agent: owns customer interaction, tone, routing decisions
│   │   ├── knowledge_service.py # RAG search service: pgvector search + returns chunks to conversation agent
│   │   ├── action_service.py    # Action execution service: order operations via tool registry, returns results to conversation agent
│   │   └── escalation.py        # Escalation logic: logs reason, preserves context, returns handoff message
│   ├── tools/
│   │   ├── registry.py          # Tool registry: defines available actions, params, permissions
│   │   ├── order_tools.py       # track_order, cancel_order, process_refund
│   │   └── customer_tools.py    # get_customer_context, get_risk_score
│   ├── guardrails/
│   │   ├── input_guard.py       # Prompt injection detection, off-topic classification
│   │   └── output_guard.py      # Hallucination check, confidence threshold, promise validation
│   ├── db/
│   │   ├── models.py            # SQLAlchemy models
│   │   ├── session.py           # DB session management
│   │   ├── migrations/          # Alembic migrations
│   │   └── seed.py              # Seed demo data: customers, orders, products
│   ├── ingestion/
│   │   ├── ingest.py            # KB ingestion script: read → chunk → embed → upsert to pgvector
│   │   └── chunker.py           # Text chunking logic (paragraph/token-based, with overlap)
│   ├── tracing/
│   │   └── setup.py             # LangSmith config — ONLY place LangSmith is imported
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── pages/
│   │   │   ├── Chat.jsx         # Customer chat interface
│   │   │   └── Admin.jsx        # Admin dashboard
│   │   ├── components/
│   │   │   ├── MessageList.jsx  # Renders message history with role-based styling
│   │   │   ├── MessageInput.jsx # Text input + send button
│   │   │   ├── TypingIndicator.jsx
│   │   │   ├── CSATWidget.jsx   # Star rating shown at conversation end
│   │   │   └── ConversationLog.jsx  # Admin: conversation table with filters
│   │   └── hooks/
│   │       └── useSSE.js        # SSE connection hook for streaming agent responses
│   ├── package.json
│   └── vite.config.js
├── docs/
│   └── kb/                      # Knowledge base source documents (markdown/text/PDF)
├── CLAUDE.md                    # → this file, or a copy of it
└── railway.toml                 # Railway deployment config
```

---

## Database schema

All tables in one PostgreSQL instance. pgvector extension enabled.

### customers
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| name | VARCHAR | |
| email | VARCHAR | UNIQUE |
| created_at | TIMESTAMP | |
| metadata | JSONB | Flexible field for additional customer attributes |

Risk score is NOT stored. It is computed on the fly from refund history, complaint frequency, and escalation history when customer context is loaded. This avoids dependency on an external system keeping the score updated. Compute function lives in `backend/tools/customer_tools.py`.

### products
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| name | VARCHAR | |
| category | VARCHAR | electronics, clothing, home_goods, accessories, gift_cards, digital, personalized, perishable, hazardous |
| price | DECIMAL | |
| return_window_days | INT | Product-specific return policy (e.g. 14 for electronics, 30 for clothing) |
| warranty_months | INT | NULL if no warranty |
| final_sale | BOOLEAN | Default FALSE. Final Sale items cannot be returned or refunded under any circumstances |
| metadata | JSONB | Flexible: weight, dimensions, special handling notes |
| created_at | TIMESTAMP | |

Note: categories `gift_cards`, `digital`, `personalized`, `perishable`, and `hazardous` are non-returnable regardless of `final_sale` flag or return window.

### orders
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| customer_id | UUID | FK → customers |
| status | VARCHAR | placed, shipped, delivered, cancelled, refunded |
| total_amount | DECIMAL | |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |
| delivered_at | TIMESTAMP | NULL until delivered. Primary anchor for return window calculations; falls back to updated_at if NULL |

### order_items
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| order_id | UUID | FK → orders |
| product_id | UUID | FK → products |
| quantity | INT | |
| price_at_purchase | DECIMAL | Price at time of order (may differ from current product price) |

### refunds
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| order_id | UUID | FK → orders |
| customer_id | UUID | FK → customers (denormalized for fast risk score queries) |
| amount | DECIMAL | Refund amount (may be partial) |
| reason | VARCHAR | defective, changed_mind, wrong_item, late_delivery, other |
| status | VARCHAR | requested, approved, rejected, processed |
| initiated_by | VARCHAR | customer, agent, system |
| conversation_id | UUID | FK → conversations, NULL if initiated outside chat |
| created_at | TIMESTAMP | |
| processed_at | TIMESTAMP | NULL until processed |

### conversations
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| customer_id | UUID | FK → customers |
| status | VARCHAR | active, resolved, escalated |
| started_at | TIMESTAMP | |
| ended_at | TIMESTAMP | NULL if active |
| summary | TEXT | Generated after 60 days, replaces full message history |
| messages_purged | BOOLEAN | Default FALSE. Set TRUE after 60-day summarization job deletes messages |
| csat_score | INT | 1-5, NULL until rated |
| csat_comment | TEXT | Optional free text |

### messages
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| conversation_id | UUID | FK → conversations |
| role | VARCHAR | customer, agent, system |
| content | TEXT | |
| agent_type | VARCHAR | NULL for customer msgs; conversation, knowledge_service, action_service, escalation |
| created_at | TIMESTAMP | |

### audit_logs
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| conversation_id | UUID | FK → conversations |
| message_id | UUID | FK → messages. Links this audit entry to the specific agent response it produced |
| agent_type | VARCHAR | Which agent acted |
| action | VARCHAR | What it did (e.g. search_kb, cancel_order, escalate) |
| input_data | JSONB | What went in |
| output_data | JSONB | What came out |
| routing_decision | VARCHAR | Why conversation agent called this service |
| confidence | FLOAT | Agent's confidence in its response |
| created_at | TIMESTAMP | |

### escalations
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| conversation_id | UUID | FK → conversations |
| reason | VARCHAR | customer_requested, low_confidence, unknown_intent, policy_exception, repeated_failure |
| agent_confidence | FLOAT | Confidence score at time of escalation |
| context_summary | TEXT | Brief summary of what happened before escalation |
| created_at | TIMESTAMP | |

### kb_documents
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| filename | VARCHAR | Original filename/path |
| title | VARCHAR | Document title |
| category | VARCHAR | returns, shipping, payments, products, account, warranty |
| version | INT | Incremented on re-ingestion |
| ingested_at | TIMESTAMP | |
| metadata | JSONB | Flexible: author, source URL, review date |

### kb_chunks (pgvector)
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| document_id | UUID | FK → kb_documents. Delete all chunks for a document on re-ingestion |
| chunk_text | TEXT | The actual text chunk |
| chunk_index | INT | Position within source document |
| embedding | VECTOR(1536) | pgvector column, dimension matches embedding model |
| created_at | TIMESTAMP | |

### Indexes
Create these from day one — they support the core query patterns:
```sql
-- Chat: loading conversation messages
CREATE INDEX idx_messages_conversation ON messages(conversation_id);

-- Customer context: loading orders and refund history
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_refunds_customer ON refunds(customer_id);
CREATE INDEX idx_conversations_customer ON conversations(customer_id);

-- Audit: linking logs to conversations and messages
CREATE INDEX idx_audit_conversation ON audit_logs(conversation_id);
CREATE INDEX idx_audit_message ON audit_logs(message_id);

-- Escalations: dashboard metrics
CREATE INDEX idx_escalations_conversation ON escalations(conversation_id);
CREATE INDEX idx_escalations_reason ON escalations(reason);

-- Admin dashboard: filtering conversations
CREATE INDEX idx_conversations_status ON conversations(status);
CREATE INDEX idx_conversations_csat ON conversations(csat_score) WHERE csat_score IS NOT NULL;

-- KB: similarity search (create after initial data load for better index quality)
CREATE INDEX idx_kb_embedding ON kb_chunks USING hnsw (embedding vector_cosine_ops);

-- KB: re-ingestion cleanup
CREATE INDEX idx_kb_chunks_document ON kb_chunks(document_id);
```

---

## LangGraph state machine

### State schema
```python
class AgentState(TypedDict):
    messages: list           # Full conversation history
    customer_id: str         # Current customer
    customer_context: dict   # Loaded from DB: purchase history, risk score
    retrieved_context: list  # KB chunks returned by knowledge service (cleared each turn)
    action_results: list     # Results from action service calls this turn
    confidence: float        # Conversation agent's confidence in its response
    requires_escalation: bool
    escalation_reason: str   # Why escalation was triggered
    actions_taken: list      # Audit trail of all service calls this turn
```

### Graph nodes
1. **conversation_agent** — The only customer-facing agent. Reads the latest customer message, decides what it needs (KB lookup, action execution, or escalation), calls the appropriate service(s), and generates the final customer-facing response. Owns tone, empathy, de-escalation. All intent classification happens here — no separate supervisor.
2. **knowledge_service** — Not customer-facing. Embeds the query, searches pgvector for relevant KB chunks, returns raw chunks and metadata to the conversation agent. Does NOT generate a customer response.
3. **action_service** — Not customer-facing. Receives a structured action request (e.g. cancel_order with order_id), validates parameters, executes via tool registry, returns the result to the conversation agent. Does NOT generate a customer response.
4. **escalation_handler** — Triggered by the conversation agent when it decides to escalate (customer request, low confidence, policy exception). Logs escalation reason, preserves conversation context, returns handoff message. The conversation agent delivers the handoff message to the customer.

### Graph flow
The conversation agent is the central node. It can invoke services as needed within a single turn — including multiple services (e.g. look up KB for refund policy, then execute the refund action).

```
START → conversation_agent
conversation_agent → knowledge_service  (if needs KB lookup)
conversation_agent → action_service     (if needs to execute an action)
conversation_agent → escalation_handler (if decides to escalate)
knowledge_service → conversation_agent  (returns retrieved chunks)
action_service → conversation_agent     (returns action result)
escalation_handler → END
conversation_agent → END               (after composing final response)
```

### Key difference from previous design
There is no separate supervisor/triage node. The conversation agent handles intent classification, service orchestration, and response generation in one place. This ensures consistent tone across all interaction types and avoids hand-off seams between agents.

---

## Tool registry

Define actions as structured config. Each tool specifies: name, description (for LLM), parameters with types, required permissions, and the function to execute.

### v1 tools (agent-callable)
| Tool | Parameters | What it does |
|---|---|---|
| track_order | order_id | Returns order status and item details. Uses most recent order if order_id omitted |
| cancel_order | order_id, reason | Cancels order if status is `placed`. Rejects shipped, delivered, cancelled, and refunded orders |
| process_refund | order_id, amount, reason | Initiates refund for a delivered or cancelled order. Enforces final sale, non-returnable category, and return window rules. Auto-approves or flags for human review based on risk score and refund amount |

**Security note:** `get_order_history`, `get_customer_context`, and `get_risk_score` are NOT agent-callable tools. Customer context and order history are loaded by the API layer and injected into agent state before the graph runs. The agent-callable tools above (track, cancel, refund) must validate that the order_id belongs to the current customer from state — reject any order that doesn't match.

**`process_refund` eligibility rules (enforced in order):**
1. **Final sale** — reject if any product in the order has `final_sale=True`
2. **Non-returnable category** — reject if any product's category is `gift_cards`, `digital`, `personalized`, `perishable`, or `hazardous`
3. **Return window** — reject if `(now - delivered_at) > product.return_window_days`. Bypass entirely if reason is `defective` (KB policy: defective items have no return window)
4. **Pending review** — set refund status to `pending_review` (instead of `approved`) if `risk_score > 0.7` OR `refund_amount > 50`. Triggers escalation in the conversation agent.

**`risk_score` is NOT an LLM parameter.** It is injected by `action_service` from `state["customer_context"]["risk_score"]` after null-stripping, so the LLM cannot supply or override it. This follows the same principle as `get_customer_context` — sensitive customer data is always injected by the API layer, never queried by the agent.

**Implementation note:** v1 tools are mock implementations. Each tool should validate parameters, log the action to audit, and return a realistic confirmation response — but NOT connect to any external e-commerce API or build real payment/shipping integrations. The value is in the tool registry pattern, parameter validation, and audit trail, not the underlying operations. Keep the mock logic simple and deterministic (e.g., cancel_order checks order status, returns success/failure accordingly). Do not over-engineer the tools themselves.

---

## API endpoints

### Chat
- `POST /api/chat` — Send a customer message. Body: `{ conversation_id, customer_id, message }`. Returns conversation_id.
- `GET /api/chat/stream/{conversation_id}` — SSE endpoint. Streams agent response tokens as they generate.
- `POST /api/conversations` — Start a new conversation. Body: `{ customer_id }`. Returns new conversation_id.

### Admin
- `GET /api/conversations` — List conversations with filters (status, customer, date range, csat score).
- `GET /api/conversations/{id}` — Full conversation with messages and audit log.
- `GET /api/metrics` — Aggregated stats: total conversations, escalation rate, avg CSAT, resolution paths.

### Webhooks
- `POST /api/csat` — Submit CSAT rating. Body: `{ conversation_id, score, comment }`.

### Docs
- `GET /docs` — Auto-generated FastAPI docs (Swagger UI). Available in production for demo purposes.

---

## KB ingestion pipeline

Script at `backend/ingestion/ingest.py`. Run manually for demo.

1. Read documents from `docs/kb/` directory (markdown, text, PDF)
2. Split into chunks: 300-500 tokens per chunk, 50 token overlap between chunks
3. Generate embeddings via LiteLLM (using the configured embedding model)
4. Upsert to `kb_articles` table in PostgreSQL with pgvector

### Demo KB content to generate
Create synthetic e-commerce KB documents covering:
- Return and refund policies (standard, Black Friday, electronics exceptions)
- Shipping and delivery info (timelines, international, tracking)
- Payment methods and billing issues
- Account management (password reset, email change, account deletion)
- Product warranties and support
- FAQ for common issues

---

## Guardrails

### Input guardrails (run before conversation agent)
- Classify input for prompt injection attempts — reject with safe message
- Classify input for off-topic or abusive content — redirect politely
- Pass clean input to conversation agent

### Output guardrails (run after agent response, before sending to customer)
- Check for hallucinated specifics: order numbers, dates, prices that weren't in the retrieved context
- Check for impossible promises: "I've processed your refund" when the tool wasn't actually called
- If confidence < 0.7 (configurable), escalate instead of responding

---

## CSAT flow

1. When conversation status changes to `resolved`, frontend shows CSATWidget
2. Customer rates 1-5 stars, optional comment
3. Frontend POSTs to `/api/csat`
4. Score stored on conversations table
5. Available in admin dashboard for filtering and aggregation
6. Low-CSAT conversations (1-2) flagged for review

---

## Conversation memory

- Full message history stored in `messages` table
- Loaded into LangGraph state at start of each turn
- **60-day retention:** After 60 days, run a summarization job:
  1. Generate summary of conversation using LLM
  2. Store summary in `conversations.summary`
  3. Delete associated rows from `messages` table
- **Long conversation handling:** If message history exceeds context window, summarize older messages and prepend summary to recent messages (decide on exact strategy during implementation)

---

## Demo seed data

Generate synthetic data for a convincing demo:

### Customers (5-10)
Mix of: loyal customer (many orders, high value), new customer (1 order), frustrated customer (multiple refund requests, high risk score), VIP customer.

### Orders (20-30)
Various statuses: placed, shipped, delivered, cancelled, refunded. Mix of products and price points.

### Products
Electronics (laptops, phones, accessories), clothing, home goods. Enough variety to make the demo feel real.

### Sample conversations (5-10)
Pre-generated conversations showing different routing paths:
- Knowledge query → resolved
- Order tracking → resolved
- Refund request → conversation agent calls action service → resolved
- Complex issue → escalation
- Low CSAT example

---

## Build sequence

Build in this order. Each phase should be working and testable before moving to the next.

### Phase 1: Foundation
1. FastAPI project structure with routers (chat, admin, webhooks)
2. PostgreSQL schema + migrations (all tables)
3. Seed script with demo data (customers, orders, products)
4. Basic health check endpoint

### Phase 2: Knowledge agent (end-to-end)
5. KB ingestion script (chunk → embed → store in pgvector)
6. Generate demo KB documents
7. Ingest demo KB
8. Knowledge agent: pgvector search + response generation
9. Basic LangGraph: single agent hardcoded to knowledge retrieval → response (to be refactored into conversation agent + knowledge service in Phase 3)
10. SSE streaming endpoint

### Phase 3: Full agent system
11. Conversation agent: replace hardcoded supervisor with a single customer-facing agent that classifies intent, decides which services to call, and generates all customer responses
12. Knowledge service: refactor existing knowledge agent into a non-customer-facing service that returns raw KB chunks to the conversation agent
13. Action service with tool registry (track_order, cancel_order, process_refund) — returns action results to conversation agent, does not generate customer-facing text
14. Escalation logic: conversation agent decides when to escalate, escalation handler logs reason and context
15. Customer context loading (risk score, purchase history)
16. Input/output guardrails

### Phase 4: Frontend
16. React + Vite + shadcn/ui project setup
17. Chat UI: message list, input, SSE streaming, typing indicator
18. CSAT widget at conversation end
19. Build and serve from FastAPI as static files

### Phase 5: Admin + polish
20. Admin dashboard: conversation logs with filters
21. Basic metrics endpoint (escalation rate, CSAT avg)
22. Audit log viewer
23. LangSmith tracing integration
24. Railway deployment config

---

## Implementation rules

- **All LLM calls go through LiteLLM.** Never import openai, anthropic, or any provider SDK directly.
- **All tracing config lives in `backend/tracing/setup.py`.** No LangSmith imports anywhere else.
- **All actions go through the tool registry.** No hardcoded action logic in agents.
- **Every agent action gets an audit log entry.** No exceptions.
- **Prompts live in the agent files as constants.** No separate prompt files for now, but keep them at the top of each file, clearly labeled, easy to extract later.
- **Use async throughout.** FastAPI routes, DB queries, LLM calls — all async.
- **Environment variables for all config.** LLM model, API keys, DB connection string, confidence thresholds. Nothing hardcoded.
- **Customer context is injected by the API layer, never queried by the agent.** The `chat.py` router resolves the authenticated customer from the session, loads their context (profile, order history, risk score), and injects it into the agent state BEFORE the graph runs. The conversation agent and all services receive `customer_context` as read-only state — they must NEVER have tools that accept an arbitrary `customer_id` parameter. This prevents prompt injection attacks from tricking the agent into querying other customers' data. `get_customer_context` and `get_risk_score` are called by the API layer only, not registered as agent-callable tools.
