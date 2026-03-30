The confirmation gate is a structural state check — never conversation parsing.

**First call (no prior entry):** Check `actions_taken` for a `confirmation_required` entry matching the same tool + order_id. If none found, return `confirmation_required` with action details. Do not execute.

**Second call (prior entry found):** Same check — if a matching `confirmation_required` entry IS in `actions_taken`, execute the action.

**Never:**
- Read or parse conversation history to determine if the customer confirmed
- Accept a `confirmed` flag or parameter from the LLM
- Skip the check based on prompt context or conversation tone

The LLM decides whether to re-invoke the tool based on the customer's reply. The tool only checks state.
