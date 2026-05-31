#!/usr/bin/env bash
# Entry point for the HA Finance Intelligence add-on.
#
# Home Assistant writes the add-on options to /data/options.json. We translate
# the relevant ones into HAFI_* environment variables (consumed by
# app.config), run database migrations, then start the server.
set -euo pipefail

OPTIONS_FILE="/data/options.json"

read_option() {
  # read_option <json_key> <default>
  local key="$1" default="$2"
  if [ -f "$OPTIONS_FILE" ]; then
    python3 - "$OPTIONS_FILE" "$key" "$default" <<'PY'
import json, sys
path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path) as fh:
        data = json.load(fh)
    value = data.get(key, default)
    if isinstance(value, bool):
        value = "true" if value else "false"
    print(value)
except Exception:
    print(default)
PY
  else
    echo "$default"
  fi
}

export HAFI_DATABASE_PATH="$(read_option database_path /data/finance/finance.db)"
export HAFI_CURRENCY="$(read_option currency GBP)"
export HAFI_PRIVACY_MODE="$(read_option privacy_mode strict_local)"
export HAFI_MQTT_ENABLED="$(read_option mqtt_enabled false)"
export HAFI_LOG_LEVEL="$(read_option log_level INFO)"
export HAFI_PORT="8099"

# Ensure the data directory exists and is private to this add-on (backlog #26,
# #27): only the owner may read the finance data directory and database file.
DATA_DIR="$(dirname "$HAFI_DATABASE_PATH")"
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR" || true
[ -f "$HAFI_DATABASE_PATH" ] && chmod 600 "$HAFI_DATABASE_PATH" || true

echo "[run.sh] Applying database migrations..."
cd /app/backend
alembic upgrade head || echo "[run.sh] alembic upgrade failed; app will create tables on startup"

echo "[run.sh] Starting HA Finance Intelligence on port ${HAFI_PORT}..."
cd /app
exec python -m app.main
