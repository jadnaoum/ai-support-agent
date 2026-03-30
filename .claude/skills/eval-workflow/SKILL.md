---
name: eval-workflow
description: Conventions for running evals, writing rubrics, and interpreting results. Use when working on anything in evals/, eval_test_cases.xlsx, or prompts/eval_rubrics.yaml.
---

# Eval Framework

## Scoring
- Binary Pass/Fail everywhere. No partial credit.
- Every Fail gets a structured `failure_reason` from a per-sheet enum — for diagnostic filtering, not scoring complexity.

## Rubric design
- Audit process: check correctness against KB content first, THEN tighten with checkpoints. Never both in one pass.
- KB Retrieval cases need 2-3 factual checkpoints per case.
- Intent classification = "what the customer wants." Downstream behavior is a separate concern — don't conflate them.

## Failure reason distinctions
- `hallucinated_action` = the action itself was fake (agent claimed it did something it didn't)
- `hallucinated_data` = action was real but agent fabricated supporting details (case numbers, timelines, card numbers)
- These are distinct and must not be merged.

## Judge architecture
- Classification evals (Input Guard, Intent Classifier, Output Guard) — fully programmatic label comparison, no LLM calls. Haiku is the configured model string but is never invoked for these sheets.
- Sonnet for behavioral and safety evals
- Opus for one-off calibration runs (validating judge reliability)

## What good looks like
- A great agent response addresses the customer's specific concern directly, not restating policy already covered in prior turns
- Injection attempts always escalate — never ask the LLM to judge legitimate intent from manipulative input
