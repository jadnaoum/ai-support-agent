Rules for modifying any LLM prompt in `prompts/production.yaml` or `prompts/eval_rubrics.yaml`:
- Persona over prohibitions — describe who the agent IS, not a list of don'ts
- Examples over rules — 2-3 good/bad exchanges beat a list of individual rules
- Separate concerns — tone, tool guidance, and business rules go in distinct sections
- Before adding a new rule, check if an existing persona statement or example covers it; strengthen that instead
- Collapse related rules into one cohesive paragraph (e.g. "no parroting" + "no dramatic empathy" + "be direct" = one paragraph)
- Keep total prompt length as short as possible — every line competes for the LLM's attention
- Fix the principle, not the case — when an eval fails, find the general misunderstanding behind it and propose wording that would also cover situations the eval never tested. Never propose an example or rule that restates the failing case (its product, its exact phrasing, its tool result); if an example is needed, pick a different, ordinary situation that shows the same principle. A prompt full of case-specific patches turns the agent into a rule-based one. State which other situations the proposed wording covers.
