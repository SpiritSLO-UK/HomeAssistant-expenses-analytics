#!/usr/bin/env bash
# Entry point for HA Finance Intelligence. Two ways to run:
#
#  - Home Assistant add-on: HA writes the options to /data/options.json; we
#    translate the relevant ones into HAFI_* environment variables.
#  - Standalone (Docker / docker-compose, no HA): there is no options.json, so we
#    honour whatever HAFI_* environment variables you pass to the container
#    (app.config supplies sensible defaults for anything unset).
#
# Either way we then start the server. Database migrations are NOT run here: the
# app runs them in-process (app.db.migrations_runner, called from the lifespan)
# against the ACTIVE engine, so they work against an UNLOCKED encrypted database.
# Running `alembic upgrade head` here built a plain engine with no key and could
# not open an encrypted DB, crash-looping the container on every restart.
set -euo pipefail

# --- Privilege drop (backlog #372) -----------------------------------------
# The container starts as root so that /data — a runtime volume that Home
# Assistant's Supervisor mounts root-owned — can be made writable by the app
# user. We chown it to UID 10001 (best-effort: a read-only or already-correct
# mount must not crash startup), then re-exec ourselves dropped to that
# unprivileged user via gosu. The `id -u` guard makes this idempotent: the
# second pass runs as 10001 and skips the whole block, so everything below
# (options parsing, MQTT lookup, the app and its in-process migrations) runs
# unprivileged.
if [[ "$(id -u)" == "0" ]]; then
  chown -R 10001:10001 /data || true
  exec gosu 10001:10001 "$0" "$@"
fi

OPTIONS_FILE="/data/options.json"

if [[ -f "$OPTIONS_FILE" ]]; then
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
  export HAFI_AI_API_KEY="$(read_option ai_api_key '')"
  export HAFI_MQTT_ENABLED="$(read_option mqtt_enabled false)"
  export HAFI_MQTT_HOST="$(read_option mqtt_host core-mosquitto)"
  export HAFI_MQTT_PORT="$(read_option mqtt_port 1883)"
  export HAFI_MQTT_USERNAME="$(read_option mqtt_username '')"
  export HAFI_MQTT_PASSWORD="$(read_option mqtt_password '')"
  export HAFI_LOG_LEVEL="$(read_option log_level INFO)"
  # At-rest encryption "stored" unlock mode: when set, the app unlocks the
  # encrypted DB unattended on startup. Empty (the default) = "prompt" mode.
  export HAFI_DB_KEY="$(read_option db_key '')"
else
  # --- Standalone: keep the HAFI_* env vars the user passed (don't clobber them) ---
  echo "[run.sh] No ${OPTIONS_FILE} — running standalone; honouring HAFI_* environment variables."
fi

# --- Home Assistant MQTT auto-discovery (services: mqtt) -------------------
# When MQTT is enabled and no username was set manually, ask the Supervisor for
# the broker config (the Mosquitto add-on by default) so the user need not enter
# host/credentials. Manual mqtt_* options always win (we only fill in when the
# username is blank). Requires `services: [mqtt:want]` in config.yaml, which
# grants the /services/mqtt endpoint; SUPERVISOR_TOKEN is injected automatically.
# Uses python (no curl in the image) + temp files (no eval, so a password with
# shell metacharacters can't break anything). Best-effort: if the service isn't
# there we just keep the configured values.
if [[ "${HAFI_MQTT_ENABLED:-false}" == "true" && -z "${HAFI_MQTT_USERNAME:-}" && -n "${SUPERVISOR_TOKEN:-}" ]]; then
  echo "[run.sh] MQTT enabled with no manual username — asking the Supervisor for the broker..."
  MQTT_DIR="$(mktemp -d)"
  python3 - "$MQTT_DIR" <<'PY'
import json, os, sys, urllib.request

out = sys.argv[1]
req = urllib.request.Request(
    "http://supervisor/services/mqtt",
    headers={"Authorization": "Bearer " + os.environ.get("SUPERVISOR_TOKEN", "")},
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp).get("data", {})
except Exception as exc:  # no MQTT service installed / not granted / offline
    print(f"[run.sh] Supervisor MQTT service unavailable ({exc}); keeping configured values.")
    data = {}
for key in ("host", "port", "username", "password"):
    val = data.get(key)
    if val is not None and str(val) != "":
        with open(os.path.join(out, key), "w") as fh:
            fh.write(str(val))
PY
  if [[ -s "$MQTT_DIR/host" ]];     then export HAFI_MQTT_HOST="$(cat "$MQTT_DIR/host")"; fi
  if [[ -s "$MQTT_DIR/port" ]];     then export HAFI_MQTT_PORT="$(cat "$MQTT_DIR/port")"; fi
  if [[ -s "$MQTT_DIR/username" ]]; then export HAFI_MQTT_USERNAME="$(cat "$MQTT_DIR/username")"; fi
  if [[ -s "$MQTT_DIR/password" ]]; then export HAFI_MQTT_PASSWORD="$(cat "$MQTT_DIR/password")"; fi
  rm -rf "$MQTT_DIR"
  echo "[run.sh] MQTT broker: ${HAFI_MQTT_HOST}:${HAFI_MQTT_PORT} (user: ${HAFI_MQTT_USERNAME:-none}) via Supervisor."
fi

# Defaults that apply in both modes when unset.
export HAFI_PORT="${HAFI_PORT:-8099}"
export HAFI_DATABASE_PATH="${HAFI_DATABASE_PATH:-/data/finance/finance.db}"

# Ensure the data directory exists and is private (backlog #26, #27): only the
# owner may read the finance data directory and database file.
DATA_DIR="$(dirname "$HAFI_DATABASE_PATH")"
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR" || true
[[ -f "$HAFI_DATABASE_PATH" ]] && chmod 600 "$HAFI_DATABASE_PATH" || true

# Migrations run in-process at app startup (app.db.migrations_runner), against the
# active engine, so they cover the encrypted-DB case that a blind `alembic upgrade
# head` here could not open. The app FAILS HARD on a genuine migration failure
# (aborts startup, non-zero exit) unless HAFI_ALLOW_MIGRATION_FAILURE=1 is set:
# the same recovery override, now enforced inside the app.
echo "[run.sh] Starting HA Finance Intelligence on port ${HAFI_PORT}..."
# Run from the source tree, NOT site-packages: app.main resolves the bundled
# frontend (../../../frontend/dist) and the category library (../category_library)
# relative to its own file, which only point at /app/frontend/dist and
# /app/backend/app/category_library when `app` is imported from /app/backend/app.
# (pip installed the package only to pull in dependencies.)
cd /app/backend
exec python -m app.main
