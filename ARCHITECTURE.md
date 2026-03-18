# AI Customer Support Agent — Architecture decision log

> This document captures all architecture and design decisions, including what was considered
> and rejected, and why. This is YOUR reference — for client conversations, revisiting decisions,
> and understanding the reasoning behind the system.
>
> For the actionable build spec (what Claude Code should read), see BUILD_SPEC.md.
>
> Last updated: 2026-03-18

---

## Project overview

Multi-agent AI customer support system for e-commerce. Designed as a production-ready demo that can be publicly shared and eventually customized for different companies. Serves dual purpose: portfolio piece and foundation for consulting engagements.

---

## Stack decisions

### Backend: Python + FastAPI
- **Decision:** FastAPI with SSE (server-sent events) + REST endpoints
- **Why FastAPI:** Async by default (handles multiple concurrent LLM calls), auto-generated API docs at `/docs` (great for demos), native SSE support for streaming agent responses, dominant Python API framework (portfolio signal)
- **Why not Flask:** Synchronous by default, breaks with concurrent users
- **Why not Django:** Too heavy for an API-only backend, async support still maturing, wrong shape for this project
- **Why not WebSocket:** SSE is simpler for the current use case (user sends message via REST, response streams back via SSE). WebSockets add connection management complexity that only pays off for bidirectional real-time features like live typing indicators or agent-to-human handoff. Can upgrade later if needed.
- **Structure:** Split routes into clear modules from the start:
  - `chat` router — SSE streaming and message ingestion
  - `admin` router — dashboard data endpoints
  - `webhooks` router — CSAT collection, future integrations (Slack, WhatsApp, JIRA)

### Frontend: React + Vite + shadcn/ui
- **Decision:** Served directly from FastAPI backend (static files), no separate frontend hosting
- **Why:** One deployment, one URL, no CORS issues, simpler debugging. React app builds to static files that FastAPI serves. API endpoints live on same domain.
- **Tradeoff accepted:** Frontend changes require full backend redeploy. Fine for a demo project.
- **Component library: shadcn/ui** — pre-built, professional-looking components (buttons, cards, tables, modals, form inputs) built on Tailwind. Gets the chat UI and admin dashboard to "looks like a real product" fast without custom styling work. Tailwind is still available under the hood for any custom styling needed.
- **Two interfaces:**
  - **Chat UI (customer-facing):** Message list with SSE streaming, text input, typing indicator, CSAT rating widget at conversation end. Messages render differently for agent, customer, and system notifications.
  - **Admin dashboard (internal):** Conversation logs, filters, basic metrics. Scope intentionally left open — will be defined as data becomes available.
- **Build tool: Vite** — standard for React projects, replaces deprecated Create React App. Set up once, rarely touched again.
- **Dev workflow:** Run Vite dev server locally during development (hot-reloads on code change) → `npm run build` to produce static files → copy into FastAPI project for deployment.
- **Why React over alternatives:**
  - **Vue.js:** Functionally equivalent, slightly easier learning curve. Smaller ecosystem and job market. React has more chat widget libraries, dashboard component kits, and SSE integration examples.
  - **Svelte:** Excellent developer experience, smaller bundle sizes. But smaller ecosystem, fewer pre-built components, weaker market recognition for portfolio signal.
  - **Plain HTML + JavaScript:** Works for the chat UI alone, but breaks down when adding admin dashboard — tables, filters, charts. Would mean rebuilding what React gives for free.
  - **Streamlit / Gradio:** Fastest to prototype (pure Python, no JavaScript). But looks like a prototype — recognizable "data science demo" aesthetic undermines production-ready positioning. Fine for internal testing, wrong for a public-facing demo.
- **Previous decision (reversed):** Originally planned Vercel for frontend hosting. Dropped because the added complexity isn't justified — we're serving a static SPA, not using SSR or edge functions.

### Hosting: Railway (single container)
- **Decision:** Single Railway container serving FastAPI + static React frontend
- **URL:** Default Railway URL for sharing (`yourapp.up.railway.app`), with option to add custom domain later for client demos
- **Watch out for:** Cold starts on Railway can affect demo experience. Consider keeping the instance warm if demoing live.
- **Consideration for later:** If costs grow, evaluate Fly.io or a small Hetzner VPS.

### Orchestration: LangGraph
- **Decision:** LangGraph for multi-agent orchestration
- **Why:** Routing logic (triage → knowledge/action/escalation) maps naturally to a state graph. Provides visibility into agent transitions, which feeds into audit logging. Native integration with LangSmith for tracing. Explicit graph gives deterministic routing — when a customer asks for a refund, triage → action agent → execute, not agents having a conversation about it.
- **Graph structure:** Supervisor node (triage) routes to one of three agent nodes based on intent classification.
- **Known tradeoff:** Steeper learning curve, fast-evolving API can break tutorials, multiple layers of abstraction to debug. Worth it for the control and observability it provides.
- **Alternatives considered:**
  - **n8n / Zapier:** Good for linear service-to-service workflows (and already used for KB ingestion, content pipelines). Wrong for conversational AI — no native support for conversational state management, confidence-based routing, or agent-to-agent handoff with shared context. n8n may still be used for peripheral workflows (KB ingestion, future Slack/JIRA integrations).
  - **CrewAI:** Easier to learn, role-based collaboration model. But less control over routing logic — confidence thresholds and risk-based policy decisions need explicit graph control, not autonomous agent collaboration.
  - **Microsoft AutoGen:** Conversational agent-to-agent approach. Powerful for open-ended research, too unpredictable for deterministic customer support routing.
  - **OpenAI Agents SDK:** Simpler, but locks into OpenAI ecosystem — conflicts with LiteLLM model-agnostic decision.
  - **Google ADK:** Same ecosystem lock-in concern (Gemini-native), smaller community.
  - **PydanticAI:** Good for single-agent structured tasks, not designed for multi-agent orchestration.
  - **Single LLM with tools (no framework):** Dramatically less code (~50 lines vs ~500). Works for simple cases. But loses: explicit routing control, separation of agent permissions/tools, structured traces for audit logging, and portfolio signal. The spec's requirements (confidence-based escalation, customer risk routing, audit logging, multi-agent separation) justify the orchestration overhead.

### Prompt management: Not in v1, designed for later
- **Decision:** No dedicated prompt management tool now. Prompts live in code, versioned with git.
- **Future option: PromptLayer** (or prompt management features in LangSmith/Langfuse). Would allow non-technical team members to edit agent prompts through a visual dashboard without code deploys. Relevant when pitching to companies where a customer success manager wants to tweak agent tone independently.
- **Note:** PromptLayer is NOT an orchestration tool — it's a prompt CMS/versioning layer that sits alongside tracing tools, not a replacement for LangGraph.

### LLM calls: LiteLLM
- **Decision:** LiteLLM wraps all LLM calls
- **Why:** Model-agnostic from day one. Can swap between OpenAI, Anthropic, open-source models without changing agent code. Aligns with spec requirement for running on local/cloud machine with open-source LLM.

### Database: PostgreSQL + pgvector (single database)
- **Decision:** One PostgreSQL instance handles both structured data and vector search (via pgvector extension)
- **Structured data:** Conversation sessions, audit logs, CSAT scores, customer context (purchase history, refunds, risk profile), order data
- **Vector search:** KB article embeddings stored in a pgvector-enabled table alongside metadata (article title, category, last updated). Knowledge agent queries the same database for both customer context and KB search.
- **Deployment:** Railway managed PostgreSQL with pgvector extension enabled
- **Why single database over separate vector DB:**
  - One fewer service to deploy, configure, and pay for on Railway
  - No CORS or cross-service connection management
  - Knowledge agent queries one database instead of two (customer context + KB search)
  - Demo KB will be small (dozens to hundreds of documents) — pgvector handles this without performance issues
  - Simpler to explain to clients: "it's all in one database"
- **Alternatives considered:**
  - **Qdrant:** Purpose-built vector DB, better performance at scale (millions of vectors, sub-millisecond latency). Overkill for demo-scale KB. Migration path: if a client's KB grows beyond pgvector's performance ceiling, swap vector search to Qdrant without changing the rest of the system.
  - **Pinecone:** Managed vector DB SaaS, zero ops. But proprietary with vendor lock-in — conflicts with self-hosting requirement.
  - **ChromaDB:** Simplest option, good for prototyping. Not production-grade — persistence and scaling are weak.
  - **FAISS:** In-memory library, no persistence or API. Good for research, wrong for an application.
  - **MySQL:** Would work for structured data but weaker JSON column support and no vector extension as mature as pgvector.
  - **MongoDB:** Document DB — loses relational query capability needed for risk scoring ("all conversations for customers with more than 2 refunds in 90 days").
- **KB ingestion pipeline:** Neither pgvector nor any vector DB handles chunking or embedding — that's your responsibility. Pipeline: read document → chunk it (by paragraph/section, 200-500 tokens per chunk, with ~50 token overlap) → embed each chunk (via embedding model through LiteLLM) → upsert to pgvector table. For demo: standalone Python script run manually. For production: automated pipeline (n8n could handle this).
- **Key insight:** Chunking strategy has a bigger impact on RAG quality than database choice. Spend more time on chunk sizes and overlap than database configuration.

### Tracing and evals: LangSmith (with migration path to Langfuse)
- **Decision:** Start with LangSmith, keep integration isolated for easy swap
- **Why LangSmith first:** Zero-config integration with LangGraph, free tier sufficient for demo, fastest path to working tracing
- **Why keep Langfuse as option:** Open source (MIT), self-hostable, framework-agnostic. Important if a client requires data sovereignty or self-hosting. Migration is roughly a one-day effort if tracing config is isolated.
- **Implementation rule:** All tracing configuration lives at the config layer. No LangSmith-specific calls scattered through agent logic.

---

## Architecture layers (request flow)

```
Customer message
      │
      ▼
┌─────────────────┐
│  FastAPI backend │  (SSE + REST)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Guardrails    │  Input validation, prompt injection detection,
│                 │  output validation, confidence thresholds
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ LangGraph       │  Triage + intent routing
│ Supervisor      │
└──┬─────┬─────┬──┘
   │     │     │
   ▼     ▼     ▼
┌─────┐┌─────┐┌──────────┐
│ KB  ││Action││Escalation│
│Agent││Agent ││ Handler  │
└──┬──┘└──┬──┘└──────────┘
   │      │
   ▼      ▼
┌──────────────────────────┐
│ PostgreSQL + pgvector    │
│ (structured + vectors)   │
└──────────────────────────┘
```

### Cross-cutting concerns (not in request flow)
- **LiteLLM** — wraps every LLM call from supervisor and agents
- **LangSmith** — traces full graph execution asynchronously in background
- **Audit logging** — writes to PostgreSQL as part of request handling

---

## Agent design decisions

### Knowledge agent
- Searches pgvector for KB articles via RAG and reads customer context (purchase history, risk score) from the same PostgreSQL database
- Single database query layer: "who is this customer" (structured tables) + "what's the answer to their question" (pgvector similarity search)

### Action agent
- Executes order operations (cancel, track, refund) through a defined API layer
- **Tool registry:** Structured config defining what actions exist, what parameters each requires, and what permissions are needed. This is what makes the system customizable for different companies. Without it, action logic gets hardcoded.
- Actions are logged for audit

### Escalation handler
- Triggered when: customer explicitly requests human, agent confidence is below threshold, or agent doesn't know what to do
- Logs the escalation reason and conversation context

---

## Guardrails (input/output)

- **Input:** Classification to catch prompt injection and off-topic abuse
- **Output:** Validation to prevent hallucinated order details or impossible promises
- **Confidence threshold:** Below a defined threshold, agent escalates rather than guesses
- Not optional — this is what separates a demo from something production-credible

---

## Data design decisions

### Conversation memory
- **Full conversations stored for 60 days** in PostgreSQL
- **After 60 days:** Auto-summarize conversation, keep summary only
- **Session management:** Conversation history loaded per session. Need to decide on sliding window or summarization strategy for long conversations (context length concern).

### Customer context
- Stored in PostgreSQL: purchase history, refund history, past interactions
- **Risk scoring:** Customers flagged based on past negative experiences. Policies can vary based on risk level (e.g., more generous refund policy for customers who've had bad experiences)
- Context loaded by knowledge agent and action agent to personalize responses

### CSAT
- Triggered at end of conversation (post-conversation event)
- Stored in PostgreSQL
- Surfaced in admin dashboard
- **Evaluation use:** Low-CSAT conversations searchable for model improvement and fine-tuning data

### Audit logging
- All agent actions logged to PostgreSQL
- Logs include: timestamp, agent type, action taken, inputs, outputs, routing decision

---

## Admin dashboard
- Scope intentionally left open for now — will decide what to show as data becomes available
- **Minimum viable dashboard (when ready):** conversation logs with routing decisions visible, CSAT scores over time, escalation rate, average resolution path
- Built as part of the React frontend, served from same backend

---

## Future-ready considerations (not in v1, but designed for)

- **Multi-channel:** Webhook router designed to accommodate Slack, WhatsApp, JIRA integrations
- **Multi-tenant:** Tool registry and KB ingestion designed to be company-specific
- **Model flexibility:** LiteLLM abstraction allows swapping models including open-source
- **Regression testing:** Eval suite via LangSmith/Langfuse to catch performance regressions on prompt or model changes
- **Prompt testing and tracking:** Covered by tracing platform
- **Model distillation:** Low-CSAT conversations and eval data can feed fine-tuning pipelines
- **Dedicated vector DB (Qdrant):** If a client's KB scales to hundreds of thousands of documents and pgvector performance degrades, migrate vector search to Qdrant. The embedding/search logic in application code stays nearly identical — only the storage backend changes.
- **Graph database (Neo4j) and GraphRAG:** Consider adding a graph layer for enterprise clients with complex, deeply connected data. Two use cases where graphs materially improve the system:
  - **Customer 360 context:** When a client has thousands of products, complex policy hierarchies, and deep customer relationship data, graph traversal outperforms multi-table SQL joins. Example: "what policies apply to this customer given their full purchase history, product categories, and prior complaint outcomes?" is a natural graph traversal but a 5-6 table SQL join.
  - **GraphRAG for knowledge base:** When the KB has deep interconnections between documents (e.g., policies that reference other policies, product specs that cross-reference compatibility), a knowledge graph captures entity relationships that vector search alone misses. Enables multi-hop reasoning: "can I return my laptop bought during Black Friday?" requires connecting the product → promotion → modified return policy chain.
  - **When NOT to add it:** A small e-commerce KB with straightforward policies and a customer base where SQL joins are manageable. The overhead of designing a graph schema, building extraction pipelines, and maintaining the graph isn't justified until the data complexity demands it.
  - **Security/access control via graphs:** Graph databases can model permission structures (Agent Role → can access → Action Type → requires → Authorization Level). Worth considering at enterprise scale, but a simple permissions table in PostgreSQL covers current needs.
- **Prompt management (PromptLayer or similar):** When non-technical team members need to edit agent prompts without code deploys. Not needed while solo-developing, relevant when pitching to companies with dedicated customer success teams.

---

## Development tool

### Coding agent: Claude Code
- **Decision:** Claude Code as primary development tool, with Max plan ($100 or $200/month) for sufficient usage during the build phase
- **Why Claude Code:** Deep codebase reasoning — recursively explores project structure and maintains context across files. Critical for this project where agents, state graph, database schemas, and API routes all need to be coherent. CLAUDE.md file (build spec) gives persistent project context across sessions. Best MCP support for connecting to GitHub and deployment tools.
- **How to use it:** Work in focused sessions with specific tasks from the build sequence in BUILD_SPEC.md. Feed the build spec as context. Break work into discrete pieces rather than "build me the whole thing."
- **Alternatives considered:**
  - **OpenAI Codex:** More token-efficient (roughly half the cost per task), generous usage limits on $20 plan, can run tasks autonomously in cloud (hand off and come back to results). Slightly lower code quality in blind tests (Claude Code won 67% of head-to-head comparisons). Good fallback for routine tasks like frontend components if Claude Code limits become an issue.
  - **Cursor:** IDE-based, lowest learning curve, best visual interface for reviewing AI changes. At $20/month, most affordable. But less suited to the "spec-driven, build from scratch" workflow — stronger for editing existing codebases than generating from a spec.
- **Practical note:** Can use both Claude Code and Codex strategically — Claude Code for complex orchestration and agent logic where quality matters most, Codex for routine frontend components and utility scripts if limits are a concern.

---

## Decisions still open

- [ ] Sliding window vs. summarization for long in-session conversations
- [ ] Admin dashboard specific metrics and views
- [ ] Specific guardrail implementation (custom vs. library like NeMo Guardrails)
- [ ] Custom domain for demo URL
- [ ] Exact PostgreSQL schema (tables, relationships)
- [ ] LangGraph state machine definition (nodes, edges, state schema)
