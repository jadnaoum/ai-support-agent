When running evals in the background, always use `python -u` to disable output buffering so the log file updates in real time:

```bash
python -u -m evals.run_evals --tag <tag> ... > /tmp/<tag>_eval.log 2>&1 &
```

Tell the user the log path and that they can follow it with `! tail -f /tmp/<tag>_eval.log`.

Always pass `--tag`. Without it the runner falls back to an existing tag, which prevents new test cases from being written to `responses.json` (no slot exists for them in the old tag). Use `--tag` even for single-case reruns — it's required for the full agent response to be stored and retrievable.

## Output guard mode — ask before every run

Before launching ANY eval run, ask Jad whether to run with the output guard **on** or **off** — never assume either. Pass the choice explicitly:

```bash
python -u -m evals.run_evals --tag <tag> --output-guard off ...
```

- `--output-guard off` (bypass): measures agent behavior in isolation; guard API flakiness can't contaminate results. Historical default.
- `--output-guard on`: runs the real guard LLM end-to-end — required to measure guard blocking, false positives, and Option-2 handoff detection.

The Output Guard sheet's dedicated test mode always runs the real guard regardless of this flag.
