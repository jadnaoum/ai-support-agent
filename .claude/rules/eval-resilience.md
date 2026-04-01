When an eval run produces no test case output within 2 minutes of starting, do not wait — automatically diagnose and fix before retrying.

**Diagnostic checklist (run in order):**
1. `curl -s http://localhost:8000/health` — is the server up?
2. `lsof -i:8000` — is the port bound? Is the process still alive?
3. `tail -20 /tmp/server.log` — any startup errors, reload loops, or import failures?
4. Check for hung eval processes: `pgrep -f run_evals`

**Common fixes:**
- Server died or hung: kill and restart with `APP_ENV=test uvicorn backend.main:app --port 8000 > /tmp/server.log 2>&1 &`
- Server started with `--reload` and restarted mid-run due to file changes: restart without `--reload`
- Port already in use: `lsof -ti:8000 | xargs kill -9` before restarting
- Hung eval process: `kill <pid>` then retry

Always restart the server without `--reload` for eval runs — file changes during a run will cause mid-run restarts and silent failures.
