#!/usr/bin/env bash
# Entry point for HA Finance Intelligence. Two ways to run:
#
#  - Home Assistant add-on: HA writes the options to /data/options.json; we
#    translate the relevant ones into HAFI_* environment variables.
#  - Standalone (Docker / docker-compose, no HA): there is no options.json, so we
#    honour whatever HAFI_* environment variables you pass to the container
#    (app.config supplies sensible defaults for anything unset).
#
# Either way we then run database migrations and start the server.
set -euo pipefail

OPTIONS_FILE="/data/options.json"

if [ -f "$OPTIONS_FILE" ]; then
  # --- Home Assistant add-on: translate options.json -> HAFI_* env vars ---
  read_option() {
    # read_option <json_key> <default>
    local key="$1" default="$2"
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
  }
  export HAFI_DATABASE_PATH="$(read_option database_path /data/finance/finance.db)"
  export HAFI_CURRENCY="$(read_option currency GBP)"
  export HAFI_PRIVACY_MODE="$(read_option privacy_mode strict_local)"
  export HAFI_MQTT_ENABLED="$(read_option mqtt_enabled false)"
  export HAFI_MQTT_HOST="$(read_option mqtt_host core-mosquitto)"
  export HAFI_MQTT_PORT="$(read_option mqtt_port 1883)"
  export HAFI_MQTT_USERNAME="$(read_option mqtt_username '')"
  export HAFI_MQTT_PASSWORD="$(read_option mqtt_password '')"
  export HAFI_LOG_LEVEL="$(read_option log_level INFO)"
else
  # --- Standalone: keep the HAFI_* env vars the user passed (don't clobber them) ---
  echo "[run.sh] No ${OPTIONS_FILE} — running standalone; honouring HAFI_* environment variables."
fi

# Defaults that apply in both modes when unset.
export HAFI_PORT="${HAFI_PORT:-8099}"
export HAFI_DATABASE_PATH="${HAFI_DATABASE_PATH:-/data/finance/finance.db}"

# Ensure the data directory exists and is private (backlog #26, #27): only the
# owner may read the finance data directory and database file.
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
