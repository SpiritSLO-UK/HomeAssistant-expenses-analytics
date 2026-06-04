#!/usr/bin/env bash
# [BACKLOG #8 - requested in things-to-add-change-consider.md]
# Validation runner: backend tests + frontend type-check, one command.
#
#   ./scripts/test.sh
#
# Exits non-zero if anything fails, so it is safe to wire into a pre-commit
# hook or CI. Works on Linux/macOS/WSL (venv at .venv/bin) and Git Bash on
# Windows (venv at .venv/Scripts).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- locate the backend virtualenv's python ---
if [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
  PY="$ROOT/backend/.venv/bin/python"
elif [[ -x "$ROOT/backend/.venv/Scripts/python.exe" ]]; then
  PY="$ROOT/backend/.venv/Scripts/python.exe"
else
  echo "Backend venv not found. Create it with:" >&2
  echo "  python3 -m venv backend/.venv" >&2
  echo "  backend/.venv/bin/python -m pip install -e 'backend[dev]'" >&2
  exit 1
fi

echo "== Backend: pytest =="
"$PY" -m pytest -q "$ROOT/backend"

# --- locate npm (PATH, or common Windows install for Git Bash) ---
echo
echo "== Frontend: type-check (tsc) =="
if command -v npm >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  NPM=npm
elif [[ -x "/c/Program Files/nodejs/npm" ]]; then
  # Git Bash on Windows without Node on PATH: add it so npm can find node.
  export PATH="/c/Program Files/nodejs:$PATH"
  NPM=npm
else
  echo "npm/node not found on PATH. Install Node.js (LTS) and re-run." >&2
  exit 1
fi

cd "$ROOT/frontend"
[[ -d node_modules ]] || "$NPM" install
"$NPM" run lint

echo
echo "ALL CHECKS PASSED"
