#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_SCRIPT="$ROOT_DIR/scripts/start_backend.sh"
FRONTEND_SCRIPT="$ROOT_DIR/scripts/start_frontend.sh"

cleanup() {
  jobs -p | xargs -r kill >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

"$BACKEND_SCRIPT" &
BACKEND_PID=$!

"$FRONTEND_SCRIPT" &
FRONTEND_PID=$!

echo "Backend PID: $BACKEND_PID"
echo "Frontend PID: $FRONTEND_PID"
echo "Backend:  http://127.0.0.1:8000"
echo "Frontend: http://127.0.0.1:5173"

wait
