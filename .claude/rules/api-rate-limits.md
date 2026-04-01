Anthropic API rate limits — Tier 1 (update this file if our tier changes):

| Model  | RPM | Input tokens/min | Output tokens/min |
|--------|-----|-----------------|-------------------|
| Sonnet | 50  | 30,000          | 8,000             |
| Opus   | 50  | 30,000          | 8,000             |
| Haiku  | 50  | 50,000          | 10,000            |

Limits are **per model**. Agent calls (Sonnet) and judge calls (Sonnet/Haiku) each count against their own model's limit.

**Whenever writing code that makes multiple API calls in a loop** (evals, batch scripts, seeding, testing), enforce a delay between iterations. The eval harness uses `--delay 5` (5 seconds between test cases). Each eval case makes ~4 API calls across agent + judge, so 5s ≈ 48 RPM peak — just under the 50 RPM cap with headroom for retries.

Do not assume the API will absorb bursts gracefully — Tier 1 rate limiting is strict and errors are returned immediately (HTTP 429).
