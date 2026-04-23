# AI Customer Support Agent — Architecture decision log

> This document captures all architecture and design decisions, including what was considered
> and rejected, and why. This is YOUR reference — for client conversations, revisiting decisions,
> and understanding the reasoning behind the system.
>
> For the actionable build spec (what Claude Code should read), see BUILD_SPEC.md.
>
> Last updated: 2026-04-23 (long-term architectural direction added)

---

## Project overview

Multi-agent AI customer support system for e-commerce, built around a single conversation agent that owns the entire customer interaction. Knowledge retrieval and order actions are handled by backend services the conversation agent calls as needed — the customer only ever talks to one agent. Designed as a production-ready demo that can be publicly shared and eventually customized for different companies. Serves dual purpose: portfolio piece and foundation for consulting engagements.

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
- **Decision:** LangGraph for agent orchestration with a central conversation agent
- **Architecture (revised):** Single conversation agent owns the customer interaction and calls knowledge/action services as needed. No separate supervisor or triage node — the conversation agent handles intent classification, service orchestration, and response generation in one place.
- **Why this over the original supervisor-routing design:** The original plan had a supervisor classifying intent and routing to separate knowledge/action/escalation agents, each generating their own customer-facing responses. This created a risk of tone inconsistency across agents and hand-off seams in the conversation. With one conversation agent owning the customer relationship, tone and empathy stay consistent regardless of whether the customer is asking a question, requesting a refund, or being escalated. The services behind it are simpler too — they just fetch data or execute actions and return results, no prompt engineering for customer-facing text.
- **Why still use LangGraph (vs. plain function calls):** The conversation agent calling services could technically be plain Python function calls. LangGraph still earns its place because: (1) structured traces in LangSmith show exactly which services were called per turn, (2) the state graph makes the flow explicit and auditable, (3) conditional edges handle multi-step turns cleanly (e.g., KB lookup → action execution → response in one turn), (4) portfolio signal — demonstrates orchestration competence.
- **Known tradeoff:** Steeper learning curve, fast-evolving API can break tutorials, multiple layers of abstraction to debug. Worth it for the control and observability it provides.
- **Alternatives considered:**
  - **n8n / Zapier:** Good for linear service-to-service workflows (and already used for KB ingestion, content pipelines). Wrong for conversational AI — no native support for conversational state management, confidence-based routing, or agent-to-agent handoff with shared context. n8n may still be used for peripheral workflows (KB ingestion, future Slack/JIRA integrations).
  - **CrewAI:** Easier to learn, role-based collaboration model. But less control over routing logic — confidence thresholds and risk-based policy decisions need explicit graph control, not autonomous agent collaboration.
  - **Microsoft AutoGen:** Conversational agent-to-agent approach. Powerful for open-ended research, too unpredictable for deterministic customer support routing.
  - **OpenAI Agents SDK:** Simpler, but locks into OpenAI ecosystem — conflicts with LiteLLM model-agnostic decision.
  - **Google ADK:** Same ecosystem lock-in concern (Gemini-native), smaller community.
  - **PydanticAI:** Good for single-agent structured tasks, not designed for multi-agent orchestration.
  - **Single LLM with tools (no framework):** Dramatically less code (~50 lines vs ~500). Works for simple cases. But loses: explicit routing control, separation of agent permissions/tools, structured traces for audit logging, and portfolio signal. The spec's requirements (confidence-based escalation, customer risk routing, audit logging, service separation) justify the orchestration overhead.

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
- **Vector search:** KB article embeddings stored in a pgvector-enabled table alongside metadata (article title, category, last updated). Knowledge service queries the same database for both customer context and KB search.
- **Deployment:** Railway managed PostgreSQL with pgvector extension enabled
- **Why single database over separate vector DB:**
  - One fewer service to deploy, configure, and pay for on Railway
  - No CORS or cross-service connection management
  - Knowledge service queries one database instead of two (customer context + KB search)
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
┌──────────────────────┐
│  Conversation Agent  │  Single customer-facing agent
│  (intent + tone +    │  Classifies intent, calls services,
│   response)          │  generates all customer responses
└──┬─────────────┬─────┘
   │             │
   ▼             ▼
┌──────────┐ ┌──────────┐
│Knowledge │ │ Action   │  Not customer-facing
│ Service  │ │ Service  │  Return raw data/results
└────┬─────┘ └────┬─────┘
     │             │
     ▼             ▼
┌──────────────────────────┐
│ PostgreSQL + pgvector    │
│ (structured + vectors)   │
└──────────────────────────┘
```

Escalation is a decision the conversation agent makes — not a separate service. When it triggers, the escalation handler logs the reason and context, and the conversation agent delivers the handoff message to the customer.

### Cross-cutting concerns (not in request flow)
- **LiteLLM** — wraps every LLM call from the conversation agent (knowledge and action services don't make LLM calls — they query the DB and execute tools)
- **LangSmith** — traces full graph execution asynchronously in background
- **Audit logging** — writes to PostgreSQL as part of request handling

---

## Agent design decisions

### Architecture change: single conversation agent (revised from supervisor-routing model)
- **Original design:** Supervisor node classifies intent and routes to one of three separate agents (knowledge, action, escalation), each generating their own customer-facing responses.
- **Revised design:** One conversation agent owns the entire customer interaction. It classifies intent itself, calls knowledge and action services for data/execution, and generates all customer-facing text. Services are not customer-facing — they return raw data.
- **Why the change:**
  - **Tone consistency:** With three agents each generating customer responses, tone and personality could vary between a knowledge answer and a refund confirmation. One agent means one voice.
  - **Simpler prompt engineering:** Only one agent needs a customer-facing system prompt with tone, empathy, de-escalation instructions. Services just need functional prompts (or none at all — they can be pure code).
  - **Multi-step turns:** A customer saying "I want to return my broken laptop" needs both KB lookup (return policy) and action execution (initiate refund) in one turn. With the supervisor model, this required routing to two agents sequentially. With one conversation agent, it naturally calls both services and synthesizes the response.
  - **Cleaner separation of concerns:** The conversation agent is responsible for the customer relationship. Services are responsible for data and execution. No blurred lines.

### Conversation agent
- The only customer-facing component. Owns tone, empathy, de-escalation, and all dialogue decisions.
- Classifies intent from the customer message (no separate supervisor) and decides which services to invoke.
- Can call multiple services in a single turn when the customer's request requires it.
- Decides when to escalate based on: customer explicitly requesting human, low confidence, policy exceptions, repeated failures.

### Knowledge service
- Not customer-facing. Returns raw KB chunks and metadata to the conversation agent.
- Searches pgvector for KB articles via RAG. The conversation agent provides the search query.
- Single database query layer: "who is this customer" (structured tables) + "what's the answer to their question" (pgvector similarity search)

### Action service
- Not customer-facing. Returns structured action results to the conversation agent.
- Executes order operations (cancel, track, refund) through the tool registry.
- **Tool registry:** Structured config defining what actions exist, what parameters each requires, and what permissions are needed. This is what makes the system customizable for different companies. Without it, action logic gets hardcoded.
- Actions are logged for audit.

### Escalation
- Not a separate agent or service — it's a decision triggered by either the agent or a tool result.
- Two concerns are intentionally separated: customer-facing response (LLM-generated) and backend side effects (escalation record, conversation status change, turn state cleared).
- For tool-driven escalation (e.g., `requires_escalation` on defective items), the LLM generates the response with KB policy details and handoff language, then side effects fire after response generation. Customer gets a rich response AND a real escalation.
- For other escalation paths (abusive input, output guard blocks, repeated failures, customer-requested), the LLM doesn't generate a response — a canned handoff message is used and side effects fire as before.
- **Why split the concerns:** The original design had a single `_do_escalate()` function that overwrote the LLM response with a generic handoff message. This forced a false choice between "rich KB-informed response" and "real backend escalation." Splitting them lets the agent surface relevant policy (what the customer is entitled to) while still triggering proper handoff.

#### Escalation when no tool can execute (Path 5)
- **Gap:** When the intent classifier returns no action (e.g., partial delivery, ambiguous damage claim), the agent routes to `knowledge_query`. If the KB can't resolve the issue, the LLM generates handoff language ("I'm connecting you with a specialist") — but no structural escalation fires. No `Escalation` row is written and `conversation.status` stays `active`.
- **Why this happens:** All structural escalation paths either go through `_do_escalate()` (input guard, output guard, repeated failures, customer request) or through the `requires_escalation` flag returned by a tool. The `knowledge_query` path has neither — the LLM generates handoff language autonomously, with no hook back to the escalation machinery.
- **Planned fix:** Extend the output guard to detect handoff language in the agent's response. When detected, fire `run_escalation_side_effects` directly (same path as tool-driven escalation). This closes the gap without adding a new graph node.

#### Output-guard-triggered escalation (planned)
- **Decision:** Output guard will be extended to scan the agent's drafted response for handoff phrases (e.g., "connecting you with a specialist," "escalating to our team"). On detection, `run_escalation_side_effects` fires before the response is sent.
- **Why output guard and not a new graph node:** A new node would require routing logic to decide when to invoke it — recreating the classification problem the output guard already solves. The output guard already sees every response; it's the natural choke point for ensuring structural side effects match stated LLM intent.
- **Tradeoff:** False positives (guard fires on figurative language) write spurious escalation records. Acceptable — a spurious escalation record is far less harmful than a real escalation with no record. Phrase list will be kept narrow and literal.

### Tool design principles

#### Read/write tool separation
- **Decision:** Eligibility checks are separate read-only tools (`check_refund_eligibility`, `check_cancel_eligibility`), distinct from their write counterparts (`process_refund`, `cancel_order`). Both share validation logic via a common internal function — no duplicated business rules.
- **Why separate tools instead of a dry-run flag:** A dry-run flag on `process_refund` forces the LLM to make a high-stakes parameter decision: is the customer asking a question (`dry_run: true`) or requesting action (`dry_run: false`)? Misclassification in one direction accidentally processes a refund. Separate tools make tool selection the only decision — "Can I get a refund?" maps to `check_refund_eligibility`, "I want a refund" maps to `process_refund`. This is a much more natural classification for LLMs, especially with native tool calling, and produces a cleaner eval surface (test tool selection accuracy directly).
- **Why not LLM-side eligibility checking:** The LLM could read KB articles about return windows and check order dates from customer context. But LLMs are unreliable at date arithmetic and rule application. Business rules (return windows, final sale flags, non-returnable categories) belong in deterministic code, not in LLM interpretation of natural language policy chunks.

#### Confirmation gate on state-changing tools
- **Decision:** All state-changing tools (`process_refund`, `cancel_order`) always reject on first invocation for a given action + order_id combination within a conversation. First call returns `confirmation_required` plus eligibility details. Second call for the same action + order_id checks `actions_taken` in agent state for a prior `confirmation_required` entry — if found, executes.
- **Why structural, not semantic:** The check is a state lookup, not LLM parsing of customer words. The tool never reads conversation history to determine if the customer said "yes." The LLM decides whether to make the second call based on the customer's response — but interpreting "yes, go ahead" is a much easier judgment than distinguishing "Can I get a refund?" from "I want a refund." The confirmation gate moves the critical decision to the easy problem.
- **Why not let the LLM set a `confirmed` flag:** If the LLM can bypass the gate, then a misclassification can bypass it too. The structural two-call pattern is the guarantee.
- **Tradeoff accepted:** Every state-changing action becomes a 2-turn minimum, even when intent is obvious. Acceptable because the cost of an unwanted action (accidental refund/cancellation) is high.
- **Eliminates prompt instructions for confirmation.** The tool forces the pattern regardless of what the prompt says — one fewer thing for the LLM to get wrong.

#### Tool responses as workflow drivers
- **Decision:** Tool rejections include an `available_action` field hinting at the logical next step (e.g., `check_refund_eligibility` returning `return_required` also returns `available_action: initiate_return`). This is a nudge, not a command — the LLM decides whether to follow it.
- **Why:** Helps the agent chain tool calls without the prompt defining per-scenario workflows. Combined with the layered workflow strategy (see below), this means the tools themselves encode most workflow logic through their constraints and hints.
- **Orchestration-agnostic requirement:** The `available_action` field references domain actions (e.g., `initiate_return`), never orchestration mechanics (e.g., not `next_pending_service: "action"`). This ensures tool responses work identically in both graph loop mode and future native tool calling mode.
- **`available_action` must reference tools that succeed if called now.** If a `cancel_order` rejection sets `available_action: "check_return_eligibility"` for a *shipped* order, the agent would offer a check that the eligibility tool would also reject (order isn't delivered yet). Future options that require waiting belong in prose (e.g., "you can return it after delivery"), not in the structured hint field.

#### Tool vs. KB responsibility split
- **Decision:** Tools answer eligibility questions (yes/no with structured reason codes). The KB provides process guidance (how to initiate a return, shipping options, timelines). The LLM combines both into a customer-facing response.
- **The LLM never calculates business rules.** It doesn't check return windows, apply final sale logic, or determine order eligibility from context data. It calls the tool, gets a structured answer, and works with that.
- **Tool rejection reasons are machine-readable** (e.g., `return_required`, `final_sale`, `outside_return_window`), not customer-facing prose. The LLM composes the customer explanation.

#### Eligibility field propagation

Write tools (`cancel_order`, `initiate_return`) call the eligibility check internally and spread its result (minus the `eligible` key) into their own response. This means any field added to an eligibility check automatically reaches the agent via `action_results` serialization. The pattern is kept for simplicity — write tools don't need to manually list which fields to propagate. The implicit contract: every field returned by an eligibility check is agent-facing. Internal/debugging fields should not be added to eligibility results.

#### Tool result shape
- **Decision:** Tool results contain no human-readable prose strings. All fields are machine-readable: structured reason codes (e.g., `reason: "already_shipped"`), booleans (e.g., `in_return_window: true`), numeric amounts, and IDs. A canonical `OUTPUT_REASONS` frozenset in `backend/tools/constants.py` lists every valid reason code.
- **Why:** `conversation.py` serializes `action_results` as `json.dumps(action_results, indent=2)` and injects the full dict into the LLM context. A prose `detail` field in a tool result is LLM-facing text that was written by tool code, not the prompt — it bypasses all prompt discipline and gets parroted verbatim. Machine-readable fields give the LLM raw facts to compose from, not pre-written sentences to repeat.
- **`OUTPUT_REASONS` as a registry:** Every reason code is listed in `constants.py`. A reason code that isn't in the frozenset is a bug — it either has no defined behavior in the prompt or is testing an untested path. Adding a new tool outcome requires adding it to `OUTPUT_REASONS` and updating eval rubrics.

### One conversation agent for all tools (considered and rejected: LLM-per-tool separation)
- **Considered:** Separate LLMs for read-only tools vs. state-changing tools, to reduce risk of the wrong action being taken.
- **Rejected because:** Splitting into multiple LLMs doesn't eliminate the classification risk — it moves it to a router LLM that must decide which tool-specific LLM to invoke. The routing decision is the same classification problem, just with added latency, duplicated context, and coordination complexity. This effectively recreates the supervisor-routing architecture already rejected for tone consistency and handoff seam reasons.
- **The actual safety boundary** is in the tool layer: validation logic, structured rejections, and confirmation gates. These enforce correct behavior regardless of what the LLM intended. One conversation agent with safe tools is more reliable than multiple LLMs with unsafe tools.

### Layer of decision: routing in loop, language in response

**Principle:** decisions about what the agent does next — call another
tool, query KB, escalate, ask for clarification, respond — belong in
the loop decision step. The response prompt's job is to shape
customer-facing language for whatever response the loop has decided
on. Mixing the two means two LLMs make overlapping decisions, which
creates drift and inconsistency.

**Current state (Option A):** the loop decision is binary today —
loop again, or respond. It doesn't decide *what kind* of response to
write. So the response prompt has accumulated some quasi-routing
guidance ("if you can't resolve this, escalate," "if a tool requires
order_id, ask for it"). These read like response shaping but are
functionally post-response routing decisions.

**Discipline going forward:**
- Treat the response prompt as language-only by default. When you
  catch yourself adding routing logic ("if X happened, do Y"), pause
  and ask whether the decision belongs in the loop layer instead.
- When cleaning up orphaned tool signals or other prompt-layer work,
  use the opportunity to move routing-shaped guidance out of the
  response prompt where it makes sense.
- Don't add new routing rules to the response prompt unless there's a
  concrete reason the loop layer can't carry them today.

**Worth exploring later (Option B):** promote the loop decision to a
multi-class router (respond / call_tool / query_kb / escalate /
ask_clarification). Response prompt becomes purely about language
given a chosen action. This is the cleaner architecture but a bigger
change — bigger than Option 2 was. Defer until there's enough
accumulated friction with Option A to justify the migration cost. The
Option-2-style escalation gap (output guard detecting handoff
language and firing structural side effects) is the canonical example
of Option A friction: a routing decision (escalate) leaking into the
response layer, requiring a downstream patch to make it structural.

### Long-term architectural direction

**Principle reinforced by industry guidance:** push decisions into
structured code boundaries; use the LLM for judgment on genuinely
ambiguous cases, natural language understanding at input, and
natural language generation at output. Everything else executes as
code.

**Where this points for our system:** over time, migrate routing
from the response prompt into the loop decision layer (Option B —
see "Layer of decision"). The target shape:

- **Loop decision** becomes a typed router with an explicit output
  schema: `{next_action: "call_tool" | "query_kb" | "escalate" |
  "ask_clarification" | "respond_final", rationale, params}`. The
  LLM provides judgment on which action fits. The system enforces
  schema validation, allowed-action constraints, and deterministic
  post-processing per branch.
- **Response prompt** becomes language-only: "given the decided
  action is X, write customer-facing language for it." No routing
  logic. No "if this happens, do that."
- **Tool layer** continues to own business rules, eligibility math,
  confirmation gates, and structural rejections. Already mostly in
  place.
- **KB retrieval** remains the progressive-disclosure mechanism for
  policy content that would otherwise bloat the prompt.

**Warning signs to watch for (the bloat-creep checklist):**

1. Response prompt crosses ~1000 lines or grows faster than it
   shrinks after refactors.
2. A bug fix means "add another rule to the prompt" — especially a
   rule scoped to one narrow scenario.
3. Agent behavior becomes dependent on exact prompt phrasing — small
   wording changes cause regressions.
4. Changes in one prompt section cause unexpected behavior in
   another (the prompt becomes a system with hidden dependencies).
5. Reviewing a prompt change requires re-reading large adjacent
   sections to understand impact.

**What to do when a warning sign appears:**

- First ask: "is this a routing decision wearing response-prompt
  clothing?" If yes, the real fix is in the loop layer, not another
  prompt rule.
- Second: "can this logic live in tool code instead?" Business rules
  almost always belong there.
- Third: "does the KB already cover this, or could it?" Policy
  content belongs in KB, not in prompts.
- Adding a new prompt rule is the last option, not the first.

**When to commit to the Option B migration:** when the discipline
above stops being enough — when cleaning up one prompt rule reveals
three more, or when the response prompt is actively slowing
development. Until then, stay in Option A with vigilance. The
migration is bigger than Option 2 was, so it needs a real reason to
trigger.

**Related industry references (as of 2026):** Anthropic's Skills
pattern for progressive disclosure of context; LangChain's guidance
on curating context rather than cramming it into system prompts;
the general "LLMs for judgment, code for determinism" principle
articulated across many sources. The direction is well-established
— this is not a speculative bet.

### Workflow strategy: layered, not per-scenario
- **Decision:** The system uses three layers of workflow control, always preferring the lowest layer that works.
- **Layer 1 — Tool constraints (always active):** Confirmation gates, required parameters, structured rejections with `available_action` hints. This is the floor — the agent can't skip these regardless of prompt instructions or LLM reasoning.
- **Layer 2 — Autonomous LLM reasoning (always active):** The agent interprets the customer's message, selects tools, reads KB content, and decides what to say and do. General prompt principles guide behavior (e.g., "when a tool rejects, explain why and offer alternatives"). This is where the LLM earns its keep — handling novel situations, combining information sources, maintaining tone.
- **Layer 3 — Explicit workflows (selective):** Predefined step-by-step flows for specific issue types. Only added when layers 1 and 2 have been tested via evals and consistently fail for a specific scenario. Each explicit workflow is maintenance overhead — it must be updated when tools or policies change, and it adds rigidity.
- **Discipline:** Don't reach for layer 3 until evals show the agent can't handle a scenario with good tool design and prompt principles alone.

### Intent classifier output handling
- **Decision:** Intent classifier (Haiku) returns a single JSON object. Parser tries to extract from the first `{` to the last `}` and parse. On parse failure, falls back to extracting only the *first* complete JSON object via brace-matching. If both attempts fail, falls back to `intent: general, confidence: 0.5`. Parse failures and fallback successes are logged for monitoring.
- **Why take the first JSON block on parse failure:** Haiku occasionally outputs two JSON blocks in one response — an initial classification followed by post-hoc rationalization and a second JSON. Since no new information arrives between the two outputs (one customer message, one LLM completion), any "correction" is the model second-guessing itself without new evidence. Empirically, the first JSON has been the correct classification in every observed failure case; the second has been a worse re-evaluation (cherry-picking one item from a list, or unnecessary clarification requests).
- **Why both prompt tightening and parser fallback:** The intent_prompt instructs the model to produce one JSON object and not revise — this reduces the rate of self-correction but does not eliminate it. The model's self-correction is driven by a belief about system constraints (e.g., "the system handles one action at a time"), not just wording. Prompt-only enforcement fails when the model's prior contradicts the prompt's claim. The parser fallback is the deterministic safety net that catches what the prompt doesn't prevent.
- **Native tool calling note:** The future migration to Anthropic's `tools` parameter eliminates this class of issue entirely — structured outputs are enforced at the API layer, not parsed from free text. Both the prompt instruction and the fallback parser become unnecessary once migrated.

### Dead-end handling: Business Limitations KB article
- **Decision:** When the agent hits a dead end (no matching tool, no specific KB article), it performs a broader KB query. If it retrieves a "Business Limitations" article that matches the customer's request, it tells the customer it's not possible — no escalation. If KB returns nothing relevant, the agent escalates (safe default).
- **Why this over KB metadata tags:** Metadata tags (`ai_actionable`, `business_limitation`) on every article require ongoing tagging discipline across the entire KB. A single catch-all article is one document to maintain, updated through the same workflow as any other KB content.
- **Why this over tool registry scope:** Maintaining a parallel list of "things humans can do" alongside the actual tools drifts over time and duplicates knowledge.
- **Graceful failure:** If the article isn't retrieved when it should be, the agent over-escalates — a human gets a case they can't help with either. That's wasted time, not a wrong answer. Over-escalation is a tuning problem; wrong answers are a trust problem.

### Item-name resolution (designed, deferred)

**Problem:** customers refer to purchases by item name ("cancel my
earphones") rather than order ID. Action tools currently require
order_id and reject with `order_id_required` when the customer
doesn't provide one. This causes dead-ends and, in earlier versions,
chain-of-thought leakage into the customer response.

**Current behavior (interim):** when a tool rejects with missing
order_id, the agent asks the customer for the order_id. If the
customer doesn't know it, the agent escalates via the Option 2
output-guard handoff detection path. No item→order resolution happens
in the system today.

**Full design:** a complete design for item-name resolution —
including tool contract changes, new rejection shapes (`missing_info`,
`multiple_matches`, `item_not_found`), turn_state persistence of
resolved items, classifier extraction of `item_name`, response prompt
patterns for the new rejection shapes, and a 3-commit rollout plan —
lives at `docs/item_name_resolution.md`. Implementation is deferred
until Option 2 validation and the orphaned warranty signals work are
complete.

**Why deferred:** the change touches tool contracts, classifier,
response prompt, turn_state, and eval cases. Too broad to stack on
top of in-flight Option 2 validation. The interim "ask for order_id
then escalate" fallback is cheap and closes the failure mode that
motivated the design.

**Implementation watch-outs (do not overlook when picking this up):**

1. **Multiple_matches Turn 2b relies on LLM fallback.** The design's
   primary mechanism for handling "the March one" style replies is
   the classifier re-extracting an explicit order_id from the prior
   agent response (the response prompt includes a load-bearing
   instruction to always include order_id explicitly). The fallback
   is the loop decision LLM doing natural-language disambiguation
   against `pending_matches`. This fallback is fragile — it's the
   kind of LLM-layer behavior the architecture has been moving away
   from. Before shipping, verify the classifier path is reliable
   (>80% hit rate on natural-language disambiguation replies) and
   treat the loop decision fallback as a safety net, not a primary
   path. If the classifier path turns out to be unreliable in
   practice, consider surfacing `pending_matches` directly to the
   response prompt so the agent can bind the customer's reply to an
   order_id deterministically — not via another LLM reasoning step.

2. **TTL counter needs an explicit source.** The design's TTL policy
   (3 turns without a successful tool call → clear `resolved_item`
   and `pending_matches`) needs a turn counter. The design proposes
   deriving it from `len(messages) // 2`, but this is a design
   choice, not just an implementation detail — it determines whether
   TTL counts by customer messages, assistant messages, or something
   else, and how it behaves across reconnects or partial turns. Pick
   the counter definition explicitly before implementing, add it as
   a named field on `turn_state` rather than deriving on the fly, and
   write a unit test that exercises TTL expiration.

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
- Context loaded by the conversation agent at the start of each turn to personalize responses and inform decisions

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

## Knowledge Management Architecture

### Source of Truth
Business rules and policy values live in a structured config (`policies.yaml`). 
This is the single source of truth. All other layers consume from it.

### Three Layers

**Policy Config** — Structured data (return windows, warranty periods, 
eligibility thresholds, fee amounts). Consumed by tools and used to 
validate KB content. Machine-readable, version-controlled.

**Tools** — Read policy config for deterministic enforcement. Also contain 
operational rules that should never be exposed to customers (exception 
thresholds, retention offers, approval limits). Tool logic is the only 
place sensitive business rules live — the LLM never sees them in text form.

**Knowledge Base** — Explains policies in natural language. Contains only 
information that is safe for customers to know. Optimized for RAG retrieval: 
single topic per section, fact-first, explicit applicability, customer 
vocabulary. Validated against policy config to prevent drift.

### Agent vs Customer Content
One set of facts, two presentation layers:
- **Agent-facing KB** — optimized for RAG. Clean headers, no fluff, 
  structured for chunking. Embedded and searched by the AI agent.
- **Customer-facing help center** — optimized for reading. Friendlier tone, 
  more context. Rendered from the same policy config.

The agent never sees the customer-facing version. Customers never see the 
agent-facing version. Neither contains sensitive operational rules.

### Separation of Concerns
- **KB** = what the customer is entitled to know (policy, process, timelines)
- **Tools** = what the agent can do (enforce rules, route requests, collect data)
- **Prompt** = how the agent communicates (tone, principles, behavior)

KB content is always informational — it tells the agent what to explain. 
Tool responses are always actionable — they tell the agent what to do. 
When a tool returns `requires_escalation`, the agent presents relevant KB 
policy to the customer, then hands off. The agent cannot act on KB content 
alone without a corresponding tool instruction.

### KB Authoring Principles
1. One topic per section under a `##` header
2. First sentence is the policy fact — no preamble
3. State when and to whom each policy applies
4. Use customer vocabulary alongside internal terms
5. No duplication across articles — each fact in one place
6. Sections = chunks — the authoring guide is the chunking specification
7. After editing, re-ingest and run evals before deploying
8. Validate against policy config to catch drift

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
- **LLM optimization and tiered model routing:** v1 uses a single model (Claude Sonnet 4.6) for the conversation agent. In production, not every turn needs a premium model. Future optimization path:
  - **Tiered routing:** Route simple queries (order tracking, FAQ) to a cheaper/faster model (e.g. Claude Haiku, GPT-4o Mini, Gemini Flash) and reserve the premium model for complex conversations (escalation decisions, multi-step refund flows, emotionally sensitive interactions). The conversation agent's confidence score and intent classification can drive model selection.
  - **A/B testing models:** LiteLLM makes swapping models a config change. Run the same eval suite across different models to compare quality vs. cost. Use CSAT scores and escalation rates as real-world quality signals.
  - **Open-source fallback:** For clients requiring data sovereignty or lower costs at scale, swap to a self-hosted open-source model (e.g. Llama) via LiteLLM without changing agent code. Relevant for enterprise clients or high-volume deployments where token costs become significant.
  - **When to optimize:** Not until there's real usage data. Premature model optimization is guesswork. Ship with the best model, collect CSAT and cost data, then make informed trade-offs.
- **LLM cost tracking and dashboard:** As usage grows, visibility into LLM spend becomes critical — both for internal budgeting and for demonstrating ROI to clients.
  - **Per-conversation cost tracking:** Log token counts (input + output) and model used per conversation turn. LiteLLM exposes this in its response metadata. Store alongside audit logs in PostgreSQL.
  - **Admin dashboard integration:** Add a cost panel to the admin dashboard showing: total spend over time, average cost per conversation, cost by model, cost by conversation type (knowledge vs. action vs. escalation). This is a strong demo feature — clients care about cost predictability.
  - **Budget alerts:** Set configurable spend thresholds that trigger alerts (e.g. daily/monthly caps). Prevents runaway costs from unexpected traffic spikes or prompt loops.
  - **LiteLLM proxy (optional):** For multi-model or multi-client deployments, LiteLLM's proxy server provides centralized cost tracking, rate limiting, and model routing across all API calls. Overkill for v1 but valuable at scale.
  - **Third-party options:** Tools like Cloudidr or Helicone provide plug-and-play cost dashboards with 1-2 lines of integration. Worth evaluating if building a custom dashboard isn't justified.
- **Native tool calling migration (Approach C):** The current orchestration uses manual intent classification (`_classify_intent` returns JSON) with graph-based service routing. A future migration to Anthropic's native tool calling API (`tools` parameter) would let Claude handle tool selection and chaining internally — eliminating `_classify_intent`, simplifying the graph, and gaining access to parallel tool calls and strict schema validation. LiteLLM supports the `tools` parameter across providers, preserving model portability. This is a significant architectural change (pending change #15) deferred until after baseline eval. The current graph loop approach (Approach A) is designed for clean swapability: tools reference domain concepts only (never orchestration mechanics like `pending_service`), prompts reference behavior only (never graph routing details), and only the graph definition handles orchestration logic. This separation ensures the migration touches one layer, not three.
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

- [ ] Native tool calling migration — when to execute Approach C (after baseline eval)
- [ ] Sliding window vs. summarization for long in-session conversations
- [ ] Admin dashboard specific metrics and views
- [ ] Specific guardrail implementation (custom vs. library like NeMo Guardrails)
- [ ] Custom domain for demo URL

## Decisions resolved

- [x] **Agent architecture:** Single conversation agent with backend services (not supervisor-routing to multiple customer-facing agents). See "Architecture change" in Agent design decisions above.
- [x] **PostgreSQL schema:** Defined and implemented in Phase 1.
- [x] **LangGraph state machine:** Defined in BUILD_SPEC.md — conversation agent as central node calling knowledge/action services.
- [x] **Read/write tool separation:** Eligibility checks as separate read-only tools, with shared validation logic. See "Tool design principles" in Agent design decisions.
- [x] **Confirmation gate:** Structural two-call pattern on state-changing tools. See "Tool design principles" in Agent design decisions.
- [x] **Workflow strategy:** Layered approach (tool constraints → autonomous reasoning → explicit workflows only when needed). See "Workflow strategy" in Agent design decisions.
- [x] **Dead-end handling:** Business Limitations catch-all KB article. See "Dead-end handling" in Agent design decisions.
- [x] **Multi-step orchestration:** Graph loop (Approach A) with 3-call limit, abstracted for future native tool calling migration. See "Future-ready considerations."
- [x] **One agent for all tools:** Rejected LLM-per-tool separation. See "One conversation agent for all tools" in Agent design decisions.
