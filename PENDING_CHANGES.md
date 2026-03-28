# Pending Changes

Tracked improvements not yet implemented. Each item has an ID so it can be referenced in
commit messages, run descriptions, and BUILD_SPEC.md notes.

| # | Status | Title | Notes |
|---|--------|-------|-------|
| 1 | OPEN | — | (reserved) |
| 2 | DONE | Log input guard blocked attempts to audit_logs | Implemented 2026-03-28. `log_blocked_attempt()` in `input_guard.py`; called from `conversation_agent_node` on block; skipped in test mode (`conversation_id=""`). |
