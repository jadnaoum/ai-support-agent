When running evals in the background, always use `python -u` to disable output buffering so the log file updates in real time:

```bash
python -u -m evals.run_evals --tag <tag> ... > /tmp/<tag>_eval.log 2>&1 &
```

Tell the user the log path and that they can follow it with `! tail -f /tmp/<tag>_eval.log`.

Always pass `--tag`. Without it the runner falls back to an existing tag, which prevents new test cases from being written to `responses.json` (no slot exists for them in the old tag). Use `--tag` even for single-case reruns — it's required for the full agent response to be stored and retrievable.
