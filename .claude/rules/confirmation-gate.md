The confirmation gate is a structural state check — never conversation parsing.

**First call (no prior entry):** Check `actions_taken` for a `confirmation_required` entry matching the same tool + order_id + normalized reason. If none found, return `confirmation_required` with action details. Do not execute.

**Second call (prior entry found):** Same check — if a matching `confirmation_required` entry IS in `actions_taken`, execute the action.

**Reason binding:** the match includes the reason (normalized through `_REASON_MAP`, so synonyms like broken/damaged match each other). A second call with a different reason gets a fresh `confirmation_required` — what executes is always what the customer saw. Entries with no recorded reason (persisted before this change) match any reason.

**Never:**
- Read or parse conversation history to determine if the customer confirmed
- Accept a `confirmed` flag or parameter from the LLM
- Skip the check based on prompt context or conversation tone

The LLM decides whether to re-invoke the tool based on the customer's reply. The tool only checks state.

**Turn boundary:** `_service_needs_loop` treats a `confirmation_required` result as a structural terminal for the turn — the loop decision is skipped and the agent must respond. Without this, a same-turn re-invocation would find its own confirmation entry in `actions_taken` and execute without the customer ever seeing the question. Do not re-enable looping after `confirmation_required`.

## Gate sequence

Both write tools (`cancel_order`, `initiate_return`) run: **eligibility → reason → confirmation**.
Eligibility comes first so ineligible orders are rejected immediately without prompting for a reason that will never be used. For `initiate_return`, the eligibility check is reason-aware — defective/damaged and duplicate_item claims route to `requires_escalation` instead of the standard return flow, so the reason (when already provided) must be passed into the eligibility check.
