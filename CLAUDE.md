# AI Customer Support Agent

FastAPI + LangGraph + pgvector + React (Vite/Tailwind). Python 3.9, async throughout.

Read BUILD_SPEC.md for the full build specification.

## Commands

```bash
# Run
source .venv/bin/activate
uvicorn backend.main:app --reload

# Frontend dev
cd frontend && npm run dev        # http://localhost:5173

# Test (requires support_agent_test DB)
pytest

# DB
alembic upgrade head
python -m backend.db.seed

# Evals
python -m evals.run_evals         # reads eval_test_cases.xlsx

# KB ingestion
python -m backend.ingestion.ingest
```

## Architecture

Single conversation agent (not a supervisor/router pattern). `conversation_agent` is the only customer-facing node — it classifies intent, calls knowledge/action services, and generates all responses. Services return raw data only, never talk to the customer.

Graph: `START → conversation_agent ↔ knowledge_service / action_service → END`. Escalation is handled inline via `_do_escalate()`, not as a graph node.

Prompts live in `prompts/production.yaml`. Eval rubrics in `prompts/eval_rubrics.yaml`.

## After completing a task

Decide if anything needs documenting:
- **Claude kept getting something wrong** → add a concise rule to `.claude/rules/`
- **Reusable workflow or reference** → create/update a skill in `.claude/skills/`
- **The code speaks for itself** → don't document anything
- **NEVER** append implementation summaries or build logs to this file
- This file should stay under 40 lines of content