#!/usr/bin/env bash
# [BACKLOG #8 - requested in things-to-add-change-consider.md]
# Dev convenience: start the backend (uvicorn :8099) and the frontend Vite dev
# server (:5173, proxies /api -> backend) together. Ctrl-C stops both.
#
#   ./scripts/dev.sh
#
# UI with hot reload: http://localhost:5173
# Backend serving the built UI: http://localhost:8099
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
  PY="$ROOT/backend/.venv/bin/python"
elif [[ -x "$ROOT/backend/.venv/Scripts/python.exe" ]]; then
  PY="$ROOT/backend/.venv/Scripts/python.exe"
else
  echo "Backend venv not found. See scripts/README.md for setup." >&2
  exit 1
fi

if command -v npm >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  NPM=npm
elif [[ -x "/c/Program Files/nodejs/npm" ]]; then
  export PATH="/c/Program Files/nodejs:$PATH"
  NPM=npm
else
  echo "npm/node not found on PATH." >&2
  exit 1
fi

echo "Starting backend on http://localhost:8099 ..."
( cd "$ROOT/backend" && "$PY" -m app.main ) &
BACKEND_PID=$!

# Stop the backend when this script exits (Ctrl-C, error, or normal exit).
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT

echo "Starting frontend dev server on http://localhost:5173 ..."
cd "$ROOT/frontend"
[[ -d node_modules ]] || "$NPM" install
exec "$NPM" run dev
