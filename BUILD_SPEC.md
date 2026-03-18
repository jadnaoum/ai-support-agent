# BUILD SPEC — AI Customer Support Agent

> This is the build specification. It tells you WHAT to build and HOW.
> For decision reasoning and alternatives considered, see ARCHITECTURE.md.

---

## Project summary

E-commerce customer support system with multi-agent orchestration. A triage supervisor routes customer messages to: (1) a knowledge agent that answers questions via RAG, (2) an action agent that performs order operations, or (3) an escalation handler for human handoff. Includes logging, CSAT collection, and an admin dashboard. Publicly hosted for demos.

---

## Tech stack

| Layer | Tool | Notes |
|---|---|---|
| Backend framework | Python + FastAPI | SSE for streaming responses, REST for everything else |
| Agent orchestration | LangGraph | State graph with supervisor routing to 3 agent nodes |
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
│   │   ├── supervisor.py        # Triage node: intent classification + routing
│   │   ├── knowledge_agent.py   # RAG search + customer context lookup
│   │   ├── action_agent.py      # Order operations via tool registry
│   │   └── escalation.py        # Human handoff handler
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
├── tests/
│   ├── conftest.py              # Shared fixtures: test DB, mock LLM, sample data
│   ├── test_routers/
│   │   ├── test_chat.py         # API endpoint tests for chat routes
│   │   ├── test_admin.py        # API endpoint tests for admin routes
│   │   └── test_webhooks.py     # API endpoint tests for CSAT collection
│   ├── test_tools/
│   │   ├── test_order_tools.py  # Tool logic: cancel, track, refund
│   │   └── test_customer_tools.py  # Risk score computation, context loading
│   ├── test_agents/
│   │   ├── test_supervisor.py   # Intent classification accuracy
│   │   ├── test_knowledge.py    # RAG retrieval relevance
│   │   └── test_action.py       # Action execution and validation
│   ├── test_guardrails/
│   │   ├── test_input_guard.py  # Injection detection, off-topic classification
│   │   └── test_output_guard.py # Hallucination check, confidence threshold
│   ├── test_ingestion/
│   │   └── test_chunker.py      # Chunking logic: sizes, overlap, edge cases
│   └── test_db/
│       ├── test_models.py       # Schema validation, relationships
│       └── test_seed.py         # Seed data integrity
├── evals/
│   ├── run_evals.py             # Eval runner: loads datasets, runs through system, scores, compares
│   ├── datasets/
│   │   ├── routing.json         # 50+ messages with labeled intents for supervisor eval
│   │   ├── knowledge_qa.json    # Questions + reference answers from KB for response quality
│   │   ├── action_scenarios.json # Order operations with expected tool calls and outcomes
│   │   └── e2e_conversations.json # Full multi-turn conversations with expected routing paths
│   └── baselines/
│       └── baseline.json        # Last known-good eval scores for regression comparison
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
| category | VARCHAR | electronics, clothing, home_goods, accessories |
| price | DECIMAL | |
| return_window_days | INT | Product-specific return policy (e.g. 14 for electronics, 30 for clothing) |
| warranty_months | INT | NULL if no warranty |
| metadata | JSONB | Flexible: weight, dimensions, special handling notes |
| created_at | TIMESTAMP | |

### orders
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| customer_id | UUID | FK → customers |
| status | VARCHAR | placed, shipped, delivered, cancelled, refunded |
| total_amount | DECIMAL | |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

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
| agent_type | VARCHAR | NULL for customer msgs; supervisor, knowledge, action, escalation |
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
| routing_decision | VARCHAR | Why supervisor sent it here |
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
    current_intent: str      # Classified by supervisor: knowledge_query, action_request, escalation
    confidence: float        # Agent's confidence in its response
    requires_escalation: bool
    actions_taken: list      # Audit trail of actions performed this turn
```

### Graph nodes
1. **supervisor** — Classifies intent from latest customer message. Routes to one of three agents. If confidence < threshold, routes to escalation.
2. **knowledge_agent** — Embeds the query, searches pgvector for relevant KB chunks, loads customer context, generates response with retrieved context.
3. **action_agent** — Identifies required action from tool registry, validates parameters, executes action (or logs it for demo), returns confirmation.
4. **escalation_handler** — Logs escalation reason, preserves conversation context, returns handoff message to customer.

### Routing edges
```
START → supervisor
supervisor → knowledge_agent    (if intent == knowledge_query)
supervisor → action_agent       (if intent == action_request)
supervisor → escalation_handler (if intent == escalation OR confidence < threshold)
knowledge_agent → END
action_agent → END
escalation_handler → END
```

---

## Tool registry

Define actions as structured config. Each tool specifies: name, description (for LLM), parameters with types, required permissions, and the function to execute.

### v1 tools
| Tool | Parameters | What it does |
|---|---|---|
| track_order | order_id | Returns order status and tracking info |
| cancel_order | order_id, reason | Cancels order if status allows, logs action |
| process_refund | order_id, amount, reason | Initiates refund, checks against customer risk score for auto-approval |
| get_order_history | customer_id | Returns recent orders for context |

For demo: tools can log the intended action and return a mock confirmation rather than connecting to a real e-commerce backend.

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
4. Upsert to `kb_chunks` table in PostgreSQL with pgvector

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

### Input guardrails (run before supervisor)
- Classify input for prompt injection attempts — reject with safe message
- Classify input for off-topic or abusive content — redirect politely
- Pass clean input to supervisor

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
- Refund request → action agent → resolved
- Complex issue → escalation
- Low CSAT example

---

## Testing and evaluation

### Principle
Every component gets tests when it's built, not after. Tests are not a separate phase — they are part of building each feature.

### Traditional tests (pytest)

Run with `pytest tests/` from project root. These cover all deterministic logic.

**What to test per component:**
- **DB models:** Schema creates correctly, FKs enforce, constraints hold (unique email, valid status values)
- **API endpoints:** Correct status codes, response shapes, error handling. Use FastAPI TestClient.
- **Tools:** Business logic — cancel_order rejects delivered orders, risk score computes correctly from refund history, process_refund checks risk threshold
- **Chunker:** Correct chunk sizes, overlap works, handles edge cases (empty doc, single paragraph, very long doc)
- **Guardrails:** Detects known injection patterns, rejects off-topic input, catches hallucinated order numbers in output
- **Seed data:** Seed script runs without error, creates expected number of records, relationships are valid

**Test fixtures (conftest.py):**
- Test database: separate PostgreSQL schema or SQLite for fast unit tests
- Mock LLM: fixture that returns predictable responses (use LiteLLM's mock provider or patch the call)
- Sample data factories: functions that create test customers, orders, conversations with known attributes

### LLM evaluation (evals/)

These test non-deterministic behavior — routing accuracy, response quality, end-to-end conversation flows.

**Eval datasets to create (in `evals/datasets/`):**

1. **routing.json** — 50+ customer messages, each labeled with expected intent (knowledge_query, action_request, escalation). Source: the sample conversations from seed data, plus edge cases (ambiguous messages, multi-intent messages, messages in different tones). Run through supervisor, measure classification accuracy. Target: >90%.

2. **knowledge_qa.json** — 20+ questions with reference answers derived from the KB. Each entry: `{ question, expected_answer_contains: [...], expected_kb_category }`. Run through knowledge agent, check that response contains the key facts. Use LLM-as-judge for quality scoring.

3. **action_scenarios.json** — 15+ action requests with expected tool calls. Each entry: `{ message, customer_id, expected_tool, expected_params }`. Run through action agent, verify the correct tool was called with correct parameters.

4. **e2e_conversations.json** — 5-10 full multi-turn conversations. Each entry: sequence of customer messages with expected routing path per turn and expected final conversation status. Run through the full graph, verify routing sequence matches.

**Eval runner (`evals/run_evals.py`):**
- Loads datasets, runs each through the relevant component
- Scores results (accuracy for routing, pass/fail for actions, LLM-judge scores for knowledge)
- Compares against `baselines/baseline.json` — flags regressions
- Outputs a summary: "routing: 94% (baseline 92% ✓), knowledge: 85% (baseline 88% ✗ REGRESSION)"
- After a successful run where all scores meet or beat baseline, update baseline.json

**Cross-model judge rule:** The LLM-as-judge used in evals MUST be a different model than the one powering the agents. If agents run on GPT-4o, the judge should be Claude (or vice versa). Different models have different blind spots — same-model evaluation misses errors that cross-model evaluation catches. Configure the judge model separately in `config.py` (e.g. `EVAL_JUDGE_MODEL`), distinct from the agent model (`LLM_MODEL`).

**When to run evals:**
- After any prompt change (supervisor system prompt, agent prompts)
- After swapping or updating the LLM model
- After changing routing logic or confidence thresholds
- Before deploying to Railway

### LangSmith integration
Eval datasets can also be uploaded to LangSmith as datasets for their built-in experiment tracking. This gives a visual dashboard for comparing eval runs over time. But the local eval runner is the primary tool — it works without LangSmith and runs in CI.

---

## Build sequence

Build in this order. Each phase should be working and testable before moving to the next.

### Phase 0: Repository setup
0. Initialize Git repo, create `.gitignore`, push to GitHub
1. Copy BUILD_SPEC.md into project as CLAUDE.md (or reference it)

### Phase 1: Foundation
1. FastAPI project structure with routers (chat, admin, webhooks)
2. PostgreSQL schema + migrations (all tables)
3. Seed script with demo data (customers, orders, products)
4. Basic health check endpoint
5. **Tests:** Schema validation, seed data integrity, endpoint smoke tests

### Phase 2: Knowledge agent (end-to-end)
6. KB ingestion script (chunk → embed → store in pgvector)
7. Generate demo KB documents
8. Ingest demo KB
9. Knowledge agent: pgvector search + response generation
10. Basic LangGraph: supervisor (hardcoded to knowledge agent) → knowledge agent → response
11. SSE streaming endpoint
12. **Tests:** Chunker unit tests, KB ingestion integration test, knowledge agent retrieval test
13. **Evals:** Create knowledge_qa.json dataset, run first knowledge eval, set baseline

### Phase 3: Full agent routing
14. Supervisor intent classification (routes to correct agent)
15. Action agent with tool registry (track, cancel, refund)
16. Escalation handler
17. Customer context loading (risk score computation, purchase history)
18. Input/output guardrails
19. **Tests:** Tool logic tests, guardrail tests, risk score computation tests
20. **Evals:** Create routing.json and action_scenarios.json datasets, run routing eval, set baselines

### Phase 4: Frontend
21. React + Vite + shadcn/ui project setup
22. Chat UI: message list, input, SSE streaming, typing indicator
23. CSAT widget at conversation end
24. Build and serve from FastAPI as static files

### Phase 5: Admin + polish
25. Admin dashboard: conversation logs with filters
26. Basic metrics endpoint (escalation rate, CSAT avg)
27. Audit log viewer
28. LangSmith tracing integration
29. Railway deployment config
30. **Evals:** Create e2e_conversations.json, run full eval suite, set final baselines

---

## Implementation rules

- **All LLM calls go through LiteLLM.** Never import openai, anthropic, or any provider SDK directly.
- **All tracing config lives in `backend/tracing/setup.py`.** No LangSmith imports anywhere else.
- **All actions go through the tool registry.** No hardcoded action logic in agents.
- **Every agent action gets an audit log entry.** No exceptions.
- **Prompts live in the agent files as constants.** No separate prompt files for now, but keep them at the top of each file, clearly labeled, easy to extract later.
- **Use async throughout.** FastAPI routes, DB queries, LLM calls — all async.
- **Environment variables for all config.** LLM model, API keys, DB connection string, confidence thresholds. Nothing hardcoded.
- **Write tests for every component when building it, not after.** Each phase in the build sequence includes specific tests. When building a new function, write the test in the same session. Run `pytest` before considering a phase complete.
- **Mock LLM calls in unit tests.** Tests that hit a real LLM are flaky and expensive. Use mock responses for deterministic tests. Real LLM calls only happen in the eval suite.
- **Run the eval suite before any deployment.** After changing prompts, models, or routing logic, run `python evals/run_evals.py` and check for regressions against baseline before deploying.
- **Git commit after every completed step.** Each numbered item in the build sequence gets a commit when working. Use descriptive messages: "Phase 2.6: generate demo KB documents" not "update files." Push to GitHub after each phase is complete.
- **Never commit broken code.** Run tests before committing. If tests fail, fix before committing.
- **Create a .gitignore from day one.** Exclude: `__pycache__/`, `.env`, `node_modules/`, `*.pyc`, `.venv/`, database files, and any API keys or secrets.
