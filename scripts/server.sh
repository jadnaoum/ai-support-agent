#!/usr/bin/env bash
# Start the backend server with environment loaded from .env
#
# Usage:
#   ./scripts/server.sh          # development mode (APP_ENV=development, --reload)
#   ./scripts/server.sh test     # test mode     (APP_ENV=test, no --reload)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Load .env (skip comments and blank lines; don't export lines that are already set)
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1090
    source .env
    set +a
else
    echo "ERROR: .env not found. Copy .env.example to .env and fill in your keys." >&2
    exit 1
fi

# Activate venv
source .venv/bin/activate

MODE="${1:-dev}"

if [[ "$MODE" == "test" ]]; then
    export APP_ENV=test
    echo "Starting server in test mode (APP_ENV=test) — no --reload"
    exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
else
    export APP_ENV=development
    echo "Starting server in dev mode (APP_ENV=development) — with --reload"
    exec uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
fi
