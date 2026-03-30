The confirmation gate is a structural state check — never conversation parsing.

**First call (no prior entry):** Check `actions_taken` for a `confirmation_required` entry matching the same tool + order_id. If none found, return `confirmation_required` with action details. Do not execute.

**Second call (prior entry found):** Same check — if a matching `confirmation_required` entry IS in `actions_taken`, execute the action.

**Never:**
- Read or parse conversation history to determine if the customer confirmed
- Accept a `confirmed` flag or parameter from the LLM
- Skip the check based on prompt context or conversation tone

The LLM decides whether to re-invoke the tool based on the customer's reply. The tool only checks state.

## Gate sequence differs by tool

**`cancel_order`: eligibility → reason → confirmation**
Reason does not affect cancel eligibility — it is purely determined by order status. Check eligibility first so ineligible orders (shipped, delivered) are rejected immediately without prompting for a reason that will never be used.

**`process_refund`: reason → eligibility → confirmation**
Reason affects eligibility — defective/damaged claims route to `requires_escalation` instead of standard refund processing. Reason must come first so the eligibility check has it available.
