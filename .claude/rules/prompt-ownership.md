Jad owns all prompt text in `prompts/` (production.yaml, response_guidance.yaml, eval_rubrics.yaml). Claude makes the edits, but only after Jad confirms the exact wording:
1. Show the exact before/after text for every change, however small (a single added word counts).
2. Wait for an explicit yes on that text. Approving a direction ("add a harmful category") is NOT approval of the wording — draft it and ask again.
3. Apply exactly what was confirmed. If testing shows the wording needs to change, propose the new text and ask again; never adjust it on the fly.
- Testing a candidate wording in a scratch copy (monkeypatched in a script, repo files untouched) is fine without asking, and is a good way to bring evidence to the proposal.
- `prompts/response_guidance.yaml` is LLM-facing text under the same ownership. Code (conversation.py `_result_guidance_notes`) only SELECTS snippets per turn — never writes or rewords them. New tool outcomes get their presentation guidance HERE, never as new prose in response_prompt.
