When running evals in the background, always use `python -u` to disable output buffering so the log file updates in real time:

```bash
python -u -m evals.run_evals ... > /tmp/<tag>_eval.log 2>&1 &
```

Tell the user the log path and that they can follow it with `! tail -f /tmp/<tag>_eval.log`.
