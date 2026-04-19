# CC Workflow Rule: Ask Before Making Prompt-Adjacent Changes

## Rule
Do not unilaterally modify code that shapes agent behavior. These changes
must come to Jad as a directive for approval before implementation:

### What counts as "prompt-adjacent"
- Any file in `backend/prompts/` or `prompts/production.yaml`
- Intent classifier logic (parsing, extraction, handling of LLM output)
- Loop decision logic (routing between respond/knowledge/action)
- Output guard verdict handling
- Input guard classification handling
- Response generation pipeline
- How tool results are interpreted or transformed before reaching the LLM
- Escalation decision logic (when/how to escalate)

### What is fine to modify without asking
- Tool code (eligibility rules, confirmation gates) — these are Jad's domain
  but follow the tool-first architecture principle
- Eval runner (run_evals.py, judges/) — infrastructure, not behavior
- Database/migrations
- Frontend code
- Test harnesses
- Bug fixes that don't change behavior (e.g., the tag mutation fix)

### Gray area — when in doubt, ask
If a fix could plausibly change what the agent says or does in production,
even if it seems like a "parsing bug" or "error handling," ask first.
The conversation.py JSON extraction change is a recent example — it
seemed like a bug fix, but it changes how the classifier interprets LLM
output, which is prompt-adjacent behavior.

### Workflow
When you identify a problem in prompt-adjacent code:
1. Describe the problem clearly — what's happening, why it matters
2. Propose one or more fix options with tradeoffs
3. Wait for Jad to choose a direction
4. Implement the chosen direction
5. Report what changed

Do NOT:
- Make the fix and report it as done
- Make the fix "to test a hypothesis"
- Make the fix and ask "is this okay?" after the fact

### Why this matters
Jad owns all prompt changes directly. This rule extends that ownership to
the code paths immediately around the prompts, because a change to how a
prompt's output is parsed or routed has the same behavioral impact as
changing the prompt itself. Preserving this boundary keeps prompt iteration
coherent and prevents behavioral drift between Jad's intended agent design
and what ships.
