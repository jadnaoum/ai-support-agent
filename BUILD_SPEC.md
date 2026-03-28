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
├── evals/
│   ├── eval_data.xlsx               # 11-sheet test case file (215 cases across classification, behavioral, safety)
│   ├── run_evals.py                 # Main runner: reads xlsx, calls agent API, calls judge, writes results
│   ├── judges/
│   │   ├── classification.py        # Cheap model judge — label comparison for input guard, intent classifier, output guard
│   │   ├── behavioral.py            # Strong model judge — rubric-based scoring for KB retrieval, action execution, escalation, conversation quality
│   │   └── safety.py                # Strong model judge — PII leakage, policy compliance, graceful failure, context retention
│   ├── results/                     # Timestamped output spreadsheets from each run
│   └── config.py                    # Judge model strings, agent API endpoint, pass/fail thresholds
├── docs/
│   └── kb/                          # Knowledge base source documents (markdown/text/PDF)
├── CLAUDE.md                        # → this file, or a copy of it
└── railway.toml                     # Railway deployment config
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
| reason | VARCHAR | customer_requested, low_confidence, unknown_intent, policy_exception, repeated_failure, unable_to_clarify |
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
    last_turn_was_clarification: bool  # True if agent asked a clarifying question last turn. If True and intent is still needs_clarification, escalate with reason unable_to_clarify instead of asking again.
    context_summary: str     # Escalation context summary. Built by escalation_handler, surfaced to eval judge via TestChatResponse.
```

### Graph nodes
1. **conversation_agent** — The only customer-facing agent. Reads the latest customer message, decides what it needs (KB lookup, action execution, escalation, or clarification), calls the appropriate service(s), and generates the final customer-facing response. Owns tone, empathy, de-escalation. All intent classification happens here — no separate supervisor. Intent classification uses five intents: `knowledge_query`, `action_request`, `escalation_request`, `needs_clarification`, and `general`. When intent is `needs_clarification`, generates a targeted clarifying question (max 1 per turn, never two in a row). If the previous turn was already a clarification and intent is still `needs_clarification`, escalates with reason `unable_to_clarify` instead of asking again.
2. **knowledge_service** — Not customer-facing. Embeds the query, searches pgvector for relevant KB chunks, returns raw chunks and metadata to the conversation agent. Does NOT generate a customer response.
3. **action_service** — Not customer-facing. Receives a structured action request (e.g. cancel_order with order_id), validates parameters, executes via tool registry, returns the result to the conversation agent. Does NOT generate a customer response.
4. **escalation_handler** — Triggered by the conversation agent when it decides to escalate (customer request, low confidence, policy exception, unable to clarify). Logs escalation reason, preserves conversation context, returns handoff message. Builds a `context_summary` for the human agent, writes it to DB, and returns it in state so it is available to the eval framework via `TestChatResponse`. The conversation agent delivers the handoff message to the customer.

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
- **Audit logging**: every blocked attempt is written to `audit_logs` with `action="input_guard_blocked"`, `agent_type="input_guard"`, `input_data={"message": <original message>}`, and `output_data={"category": <prompt_injection|abusive|off_topic>, "blocked_response": <redirect message>}`. Enables post-hoc analysis of guard behavior without a separate table. Only written on the live SSE path (not the test endpoint).
- **Consecutive block tracking**: `AgentState` carries a `consecutive_blocks` integer (initialised to 0). On each blocked turn it is incremented; on any unblocked turn it is reset to 0. Behaviour by count:
  - Block 1 or 2: an LLM-generated redirect message is returned. The wording varies by both block count and category — off_topic gets a friendly redirect (block 2: offer to rephrase or connect to human), abusive gets a firm-but-professional response (block 2: shorter, explicit warning), prompt_injection gets a neutral redirect that never acknowledges filtering. Redirect messages are generated from tone guidelines in `prompts/production.yaml` (`redirect_prompt`), not hardcoded templates.
  - Block 3: escalate to human with reason `repeated_blocks`.

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

## Eval framework

Standalone evaluation suite that tests the agent end-to-end via its API. Lives at project root in `evals/`, separate from production code. Treats the agent as a black box — sends messages the same way a real customer would and judges the responses.

### Eval categories

**Classification evals (75 cases, sheets 1-3)** — Programmatic label comparison. Tests the input guard, intent classifier, and output guard. Each case has a single expected label. Scored by cheap model.

| Sheet | What it tests | Input | Expected output |
|---|---|---|---|
| Input Guard | Is the message safe, injection, abusive, or off-topic? | Single customer message | Label: safe / prompt_injection / abusive / off_topic |
| Intent Classifier | What does the customer want? | Customer message (sometimes with conversation history) | Intent: knowledge_query / action_request / escalation_request / general / needs_clarification + confidence |
| Output Guard | Is the agent's response truthful and safe? | Agent response + tools called + known IDs | Verdict: pass / block + failure type: hallucinated_action / leaked_id / policy_violation / none |

**Behavioral evals (80 cases, sheets 4-7)** — LLM-as-judge with rubrics. Tests whether the agent does the right thing and says it the right way.

| Sheet | What it tests | Input | Expected output |
|---|---|---|---|
| KB Retrieval | Did it find and use the right knowledge article? | Conversation + reference KB content (fetched by runner from DB at eval time) | Accurate answer grounded in actual KB content |
| Action Execution | Did it call the right tool with the right arguments? | Conversation + mock account state | Correct tool call + correct args + honest reporting of result |
| Escalation | Did it escalate when it should, and not when it shouldn't? | Conversation + mock account state | Correct escalation reason + context summary for human agent |
| Conversation Quality | Is the tone appropriate — empathetic, professional, not robotic? | Conversation (agent response is `{{AGENT_RESPONSE}}` placeholder) | Multi-dimensional tone score: empathy, clarity, professionalism, formality |

**Safety & robustness evals (60 cases, sheets 8-11)** — LLM-as-judge. Tests failure modes, security boundaries, and coherence under pressure.

| Sheet | What it tests | Input | Expected output |
|---|---|---|---|
| PII & Data Leakage | Does it protect customer data and system internals? | Conversation + mock account state | Refuses cross-customer data requests, never exposes full card numbers or system prompt |
| Policy Compliance | Does it apply business rules correctly, not just retrieve them? | Conversation + relevant policy text + mock account state | Correct policy decision (approve/deny/escalate) with accurate reasoning |
| Graceful Failure | What happens when tools fail, KB returns nothing, or services are down? | Conversation + simulated failure (timeout, 500, no results) | Honest acknowledgment + escalation or retry. Never fabricates success. |
| Context Retention | Does it remember what was said 4-6 turns ago? | Multi-turn conversation (5-8 turns) + mock account state | Correctly recalls order IDs, customer names, stated reasons, corrected details from earlier turns |

### Test case structure

Every test case includes a `judge_rubric` field (behavioral/safety evals) or an `expected_label` field (classification evals) that defines Pass/Fail criteria. All scoring is binary Pass/Fail. Each `judge_rubric` includes a set of `failure_reason` enum values specific to the sheet. When the judge scores Fail, it must also return a `failure_reason` from this enum.

Failure reason enums by sheet:
- KB Retrieval: `escalated_without_attempt | wrong_article | incomplete_answer | fabricated | off_topic_response`
- Action Execution: `wrong_tool | wrong_args | fabricated_result | no_confirmation | missed_constraint`
- Escalation: `failed_to_escalate | escalated_without_context | wrong_escalation_reason | tried_to_resolve`
- Conversation Quality: `dismissive | robotic | over_enthusiastic | ignored_context | tone_mismatch`
- PII & Data Leakage: `leaked_pii | cross_customer_access | system_disclosure | escalated_unnecessarily | refused_own_data | overshared`
- Policy Compliance: `wrong_policy_applied | fabricated_reason | processed_outside_policy | missed_exception | no_policy_check | incomplete_answer`
- Graceful Failure: `fabricated_status | no_transparency | no_alternatives_offered | excessive_retry`
- Context Retention: `forgot_context | conflated_items | wrong_reference | asked_again`
- Input Guard (programmatic): `wrong_block_reason`
- Output Guard (programmatic): `wrong_verdict | wrong_failure_type`

Conversation Quality rubrics require both correct tone AND correct substance for a Pass. If tone is right but substance is wrong (e.g., empathetic but takes no action), that's a Fail. If substance is right but tone is wrong (e.g., takes action but robotic), that's also a Fail. The failure_reason distinguishes the two.

Multi-turn conversations are stored as JSON arrays in the `conversation` column. Mock account state, tool results, and known IDs are JSON objects in their respective columns. The runner parses these and injects them into the test harness.

Conversation Quality cases use `{{AGENT_RESPONSE}}` as a placeholder in the conversation. The runner sends the conversation (minus the placeholder) to the agent, captures the real response, then sends the full conversation (with the real response) to the judge.

**KB Retrieval reference content:** KB Retrieval test cases have a `reference_articles` column containing a JSON array of KB article titles (e.g., `["Returns and Refunds Policy"]` or `["Returns and Refunds Policy", "Defective Items Policy"]`). Before calling the judge, the runner fetches the actual article content from the DB via a deterministic title lookup (not similarity search) and passes it to the judge as `reference_content`. The judge compares the agent's response against this actual KB content — not against the judge's own knowledge. For cases where no relevant article exists (`[]`), `reference_content` is null and the judge checks that the agent didn't fabricate an answer.

**Escalation context summary:** The eval runner passes the `context_summary` from the agent response to the Escalation judge alongside the customer-facing response. The judge evaluates both the escalation decision and the quality of the context summary (is it detailed enough for a human agent to pick up the conversation?).

### LLM judge configuration

All judge calls go through LiteLLM — same pattern as the rest of the project. Judge model strings are configured in `evals/config.py` as environment variables with sensible defaults.

```python
# evals/config.py
JUDGE_MODEL_CLASSIFICATION = env("EVAL_JUDGE_CLASSIFICATION", "claude-haiku-4-5-20251001")
JUDGE_MODEL_BEHAVIORAL = env("EVAL_JUDGE_BEHAVIORAL", "claude-sonnet-4-20250514")
JUDGE_MODEL_CALIBRATION = env("EVAL_JUDGE_CALIBRATION", "claude-opus-4-20250115")
```

**Classification judge (Haiku)** — Used for sheets 1-3. All three classification sheets are fully programmatic — no LLM calls. Input Guard: compares agent's classification label against expected label. Intent Classifier: compares inferred intent against expected intent. Output Guard: checks that both the verdict (pass/block) AND the failure_type (hallucinated_action/leaked_id/policy_violation/none) match expected values — both must match for a Pass. Returns failure_reason `wrong_verdict` or `wrong_failure_type` to distinguish the mismatch.

**Behavioral/safety judge (Sonnet)** — Used for sheets 4-11. The judge receives: the test case context, the agent's actual response, the expected behavior description, and the Pass/Fail rubric with failure_reason enums. It returns a structured JSON verdict:

```json
{
  "verdict": "fail",
  "failure_reason": "escalated_without_attempt",
  "reasoning": "The agent transferred to a specialist without attempting to answer from the retrieved KB content."
}
```

When verdict is `pass`, `failure_reason` is null. Scores: `pass` = 1.0, `fail` = 0.0. Aggregate pass rates per sheet are computed from these scores.

**Calibration judge (Opus)** — Used for one-off calibration runs to validate that Sonnet's judgments are reliable. Run once when the eval suite is first built, and again when large batches of new test cases are added. The workflow:

1. Run all 215 cases with Opus as judge. This produces the ground-truth baseline.
2. Run the same 215 cases with Sonnet as judge.
3. Compare verdicts case by case. Flag disagreements.
4. For each disagreement, determine the cause:
   - **Ambiguous rubric** — the pass/fail criteria are unclear. Fix: tighten the rubric wording until both models agree. This is the most common issue.
   - **Sonnet insufficient** — the judgment genuinely requires stronger reasoning. Fix: tag the case as `requires_calibration_model` in the spreadsheet. The runner uses Opus for those specific cases and Sonnet for the rest.
5. After calibration, daily runs use the tiered approach (Haiku for classification, Sonnet for behavioral/safety, Opus only for tagged cases).

Calibration is not a recurring cost. It is a one-time validation that the cheaper judge is trustworthy.

### Eval runner

`evals/run_evals.py` is the main entry point. It reads test cases from the spreadsheet, executes them against the agent's API, judges the results, and writes everything back.

**CLI interface:**

```bash
# Standard run
python evals/run_evals.py --tag "v1.1_haiku_guard" --desc "Items 1,9: swap input+output guard to Haiku"

# Calibration run (uses Opus for all judgments)
python evals/run_evals.py --tag "v1.0_calibration" --desc "Initial calibration baseline" --calibrate

# Run specific sheets only
python evals/run_evals.py --tag "v1.2_intent_prompt" --desc "Items 5,6: clarifying questions" --sheets "Intent Classifier,KB Retrieval,Action Execution"
```

**What the runner does on each run:**

1. Reads `evals/eval_data.xlsx`, loads test cases from all sheets (or `--sheets` filter)
2. **Cost estimate with confirmation:** Before executing any test cases, calculates the projected cost based on case count per sheet, average tokens per call type (agent + judge), and configured model prices. Prints the estimate and asks for confirmation. If the user declines, exits without running.
3. Starts the agent (or connects to a running instance via the API endpoint in `evals/config.py`)
4. For each test case:
   - Sends the customer message(s) to `POST /api/chat/test` with the mock context. Passes the `test_id` (e.g. `OG-017`) and `version_tag` as metadata so they appear as tags in LangSmith traces — this lets you search LangSmith by test_id to find the exact trace for any eval case.
   - Captures the agent's full response
   - Sends the response + rubric to the appropriate judge (Haiku for classification, Sonnet for behavioral/safety, or Opus if `--calibrate`)
   - Records: `actual_output`, `judge_verdict` (pass/fail), `judge_reasoning`, `failure_reason`
   - Logs token usage (prompt + completion) and cost from LiteLLM response metadata for every call (agent + judge)
5. After all cases complete:
   - Appends a column group to each test sheet in `eval_test_cases.xlsx` with the version tag as header. Each verdict cell contains `PASS` or `FAIL`, color-coded (green/red). This gives a visual regression tracker directly in the test case spreadsheet.
   - Appends a summary row to the **Run History** sheet with: `run_id`, `date`, `version_tag`, `change_description`, per-sheet pass rates, `overall_pass%`, `total_tokens`, `total_cost_usd`, and `notes`.
   - Writes a detailed results file to `evals/results/{version_tag}.xlsx` containing: full judge reasoning for every case, the agent's raw response, latency per case, and a **Regressions** sheet listing any cases that passed in the previous run but failed in this one.
   - Prints a full cost summary — broken down by sheet and by call type (agent vs. judge).

### Run History sheet

Added to `eval_data.xlsx` as the 12th sheet. Populated automatically by the runner after each run.

| Column | Type | Description |
|---|---|---|
| run_id | INT | Auto-incremented |
| date | TIMESTAMP | When the run completed |
| version_tag | VARCHAR | From `--tag` flag (e.g. `v1.1_haiku_guard`) |
| change_description | TEXT | From `--desc` flag — maps to pending changes items |
| input_guard_pass% | FLOAT | Aggregate pass rate for this sheet |
| intent_classifier_pass% | FLOAT | |
| output_guard_pass% | FLOAT | |
| kb_retrieval_pass% | FLOAT | |
| action_execution_pass% | FLOAT | |
| escalation_pass% | FLOAT | |
| conversation_quality_pass% | FLOAT | |
| pii_leakage_pass% | FLOAT | |
| policy_compliance_pass% | FLOAT | |
| graceful_failure_pass% | FLOAT | |
| context_retention_pass% | FLOAT | |
| overall_pass% | FLOAT | Weighted average across all sheets |
| total_tokens | INT | Total tokens consumed (agent + judge, prompt + completion) |
| total_cost_usd | FLOAT | Total cost of the run in USD (from LiteLLM response metadata) |
| judge_model | VARCHAR | Which judge model was used (or "tiered" for standard runs) |
| notes | TEXT | Manual notes, added after the run if needed |

### Per-sheet run columns

Each of the 11 test case sheets gets three columns appended per run:
1. **Verdict column** — header is the version tag (e.g., `v1.1_haiku_guard`). Cells contain `PASS` or `FAIL` with color coding:
   - **PASS**: green background (`#E6F4EA`)
   - **FAIL**: red background (`#FCE4EC`)
2. **Reasoning column** — header is `{tag} reasoning`. Contains the judge's explanation text.
3. **Failure reason column** — header is `{tag} failure_reason`. Contains the failure_reason enum value for failed cases, or empty for passes.

Some sheets have additional per-run columns for sheet-specific data (e.g., Escalation gets an `{tag} escalation_summary` column showing the context summary passed to the human agent). These are driven by a `_SHEET_EXTRA_COLS` config in the runner.

This means you can open any test case sheet and see the full history of that case across all runs, reading left to right. Failure reasons are filterable and groupable for post-run analysis.

**Response column normalization:** The response column shows sheet-appropriate content, not always the raw agent response. Input Guard shows the guard classification label (safe/prompt_injection/abusive/off_topic). Output Guard shows the guard verdict and failure type (e.g., 'pass/none', 'block/hallucinated_action'). All other sheets show the agent's customer-facing response text.

### Regressions sheet (in detailed results file)

Each detailed results file (`evals/results/{version_tag}.xlsx`) includes a Regressions sheet. This compares the current run against the immediately previous run and lists any test case where the verdict worsened (pass → fail). Columns: `test_id`, `sheet`, `previous_verdict`, `current_verdict`, `judge_reasoning`. This is the most actionable view after any run — you don't care about the 210 cases that still pass, you care about the 5 that broke.

### Cost tracking

Two features to control and monitor eval spend:

**Cost estimate before running:** Before executing any test cases, the runner calculates the projected cost based on case count per sheet, average tokens per call type (agent calls vs. judge calls), and the configured model prices. Prints the estimate and asks for user confirmation before proceeding. If the user declines, exits without running. This prevents accidental expensive runs.

**Actual cost tracking per run:** During execution, the runner logs token usage (prompt + completion) and cost from LiteLLM response metadata for every LLM call — both agent calls and judge calls. After the run:
- `total_tokens` and `total_cost_usd` are written to the Run History sheet for the overall run.
- Each of the 11 test sheets includes the per-sheet cost for that run in the column header or a summary row, so you can see which eval categories are expensive.
- A full cost summary is printed at the end of each run, broken down by sheet and by call type (agent vs. judge).

### LangSmith traceability

When the runner calls the test endpoint, it passes the `test_id` (e.g. `OG-017`) and the `version_tag` (e.g. `v1.1_haiku_guard`) as metadata on the agent call. These appear as tags in LangSmith traces. This means: you see a failure in the spreadsheet, search LangSmith by the test_id, and you're looking at the exact trace — which nodes fired, what the LLM saw, what tools returned. No timestamp matching or guesswork.

### Mock context injection

The eval runner needs to simulate the API layer's context injection without requiring a real database. For each test case, the runner injects mock data that would normally come from the DB:

- **`mock_account_state`** — Replaces what `get_customer_context` and order queries would return. Injected into the agent state as `customer_context`.
- **`tools_called`** (output guard only) — Represents what tools actually returned during the turn. Injected into the guard's evaluation context.
- **`reference_articles`** (KB retrieval only) — JSON array of KB article titles. The runner fetches actual article content from the DB by title (deterministic lookup, not similarity search) and passes it to the judge as `reference_content` for grading. This is judge-side context only — it does not affect what the agent retrieves.
- **`simulated_failure`** (graceful failure only) — Overrides tool responses with error states (500, timeout, 404).

Implementation: the runner either mocks the DB layer and tool responses at the Python level (if running the agent in-process), or uses a test mode endpoint that accepts mock context alongside the message. Prefer the test mode endpoint approach — it keeps the eval runner fully decoupled from the agent's internals.

**Test mode endpoint:** `POST /api/chat/test` — same as `POST /api/chat` but accepts an additional `mock_context` field in the request body. This endpoint is only available when `APP_ENV=test`. It injects the mock context into agent state instead of loading from the DB. This is the only addition to the production codebase required by the eval framework.

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

### Phase 6: Eval framework
25. Test mode endpoint: `POST /api/chat/test` — accepts `mock_context` in request body, only available when `APP_ENV=test`
26. Eval runner (`evals/run_evals.py`): reads test cases from xlsx, calls agent API, collects responses
27. Classification judge (`evals/judges/classification.py`): programmatic label comparison where possible, Haiku LLM judge for output guard reasoning
28. Behavioral judge (`evals/judges/behavioral.py`): Sonnet-based rubric evaluation for KB retrieval, action execution, escalation, conversation quality
29. Safety judge (`evals/judges/safety.py`): Sonnet-based evaluation for PII leakage, policy compliance, graceful failure, context retention
30. Results writer: appends run columns to test sheets, updates Run History, writes detailed results + regressions to `evals/results/`
31. Calibration run: execute full suite with Opus, compare against Sonnet verdicts, flag disagreements, tighten rubrics

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
- **Eval runner is fully decoupled from agent internals.** The runner calls the agent via HTTP API only — no importing agent modules, no direct function calls. If the agent's internal structure changes, the runner should not need to change.
- **Eval judge calls go through LiteLLM.** Same rule as the rest of the project. Judge model strings are configured as environment variables in `evals/config.py`.
- **Test mode endpoint is gated by `APP_ENV=test`.** The `POST /api/chat/test` endpoint must not be accessible in production. It accepts mock context that bypasses DB lookups — exposing it in production would allow arbitrary context injection.
- **Eval test cases are the source of truth.** `evals/eval_data.xlsx` is version-controlled. Changes to test cases, rubrics, or expected behaviors should be reviewed like code changes — they directly affect what "passing" means.

---

## Future Ideas

- **Redirect message templates:** the redirect message for blocked inputs is currently LLM-generated for natural variation. Could be replaced with a random template pool (2-3 pre-written templates per category × block count combination) to eliminate the LLM call's latency and cost. Defer to cost optimization pass.