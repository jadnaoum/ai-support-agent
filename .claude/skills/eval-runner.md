# Eval Runner Commands

## Before every eval run
1. Restart the server to pick up any code/prompt changes:
   kill $(lsof -ti:8000) 2>/dev/null; sleep 1
   source .venv/bin/activate && APP_ENV=test uvicorn backend.main:app --port 8000 > /tmp/server.log 2>&1 &
   sleep 3
2. Health check: curl -s http://localhost:8000/health
3. If health check fails, check /tmp/server.log for errors
4. Calculate the cost estimate:
   - Count test cases on the sheet(s) being run
   - Estimate: ~$0.01 per case (classification sheets) or ~$0.03 per case (behavioral/safety sheets)
   - Present to user: "This will run X cases on [sheet]. Estimated cost: ~$Y. Proceed?"
5. Wait for user confirmation before executing

## Single sheet (targeted run)
source .venv/bin/activate && python -u -m evals.run_evals \
  --tag <version_tag> \
  --desc "<description>" \
  --sheets "<Sheet Name>" \
  --delay 1 \
  -y

## Full baseline
source .venv/bin/activate && python -u -m evals.run_evals \
  --tag <version_tag> \
  --desc "<description>" \
  --delay 3 \
  -y

## Background execution (when asked to run and move on)
<command> > /tmp/<tag>_eval.log 2>&1 & echo "PID: $!"
Follow with: tail -f /tmp/<tag>_eval.log

## Debugging
- After launching a background run, ALWAYS check the log within 10 seconds
  to confirm cases are executing: tail -5 /tmp/<tag>_eval.log
- If the log shows an argument error, fix the command and re-run
  without asking the user to debug
- Multiple sheets are comma-separated in a single quoted string: --sheets "KB Retrieval,Action Execution"
- Common failures: missing -y (EOFError), unquoted sheet names, server not running

## Rules
- ALWAYS include -y (user already confirmed in chat — script must not prompt again)
- ALWAYS include -u (unbuffered output for log tailing)
- Use --delay 1 for single sheets, --delay 3 for full baselines
- Never run without user confirmation first
- Always health-check first
