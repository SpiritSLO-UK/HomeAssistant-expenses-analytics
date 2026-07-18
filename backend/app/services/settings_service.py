"""User-editable settings, persisted in the ``settings`` table (spec §38).

Bootstrap/config defaults still come from environment variables (app.config);
these are the runtime-editable knobs surfaced in the Settings UI. Stored as
simple key/value strings.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as env_settings
from app.models import Setting

# Known keys and their defaults.
BASE_CURRENCY = "base_currency"
FX_MODE = "fx_mode"  # manual | frankfurter
RECEIPT_MATCH_MODE = "receipt_match_mode"  # suggest | auto
# AI (spec §7, §22). Off by default — strict local, no external calls.
PRIVACY_MODE = "privacy_mode"  # strict_local | local_llm | cloud_manual | cloud_auto | no_ai
AI_PROVIDER = "ai_provider"  # none | openai_compatible
AI_BASE_URL = "ai_base_url"  # e.g. http://localhost:11434/v1 (Ollama) or a cloud endpoint
AI_MODEL = "ai_model"
# Data retention (spec §28; backlog #78, #147). The policy is a JSON blob owned by
# retention_service; here we only hold the key + the related boolean/int knobs.
RETENTION_POLICY = "retention_policy"
RECEIPT_DELETE_AFTER_PROCESSING = "receipt_delete_after_processing"  # default false (keep receipts)
# Backup-trim policy (bound disk growth — backlog #78): how long / how large the
# safety-backup history may grow before old snapshots are pruned.
BACKUP_MAX_AGE_DAYS = "backup_max_age_days"
BACKUP_MAX_TOTAL_MB = "backup_max_total_mb"
BACKUP_MIN_KEEP = "backup_min_keep"
# Runtime log level, editable from Settings (spec §38). Mirrors the env default.
LOG_LEVEL = "log_level"
# Which MQTT sensors NOT to publish (backlog: let the user choose). JSON
# {"groups": [...], "sensors": [...]} of *disabled* sensor groups + individual
# sensor keys. Empty/unset = publish everything (the original behaviour).
MQTT_PUBLISH_SELECTION = "mqtt_publish_selection"
# Manifest of the row ids a ``load_demo`` created (JSON), so "Remove demo data"
# can delete exactly the demo's own rows and nothing a real import/user added.
DEMO_MANIFEST = "demo_manifest"
# Default cloud-AI privacy level applied to all categories at once + inherited by
# new categories, so the user need not set each one (backlog #28; per-category
# detail stays available behind the Categories "Advanced" reveal).
CLOUD_AI_PRIVACY_DEFAULT = "cloud_ai_privacy_default"
# Explicit OCR on/off (Settings → Services). On by default; when off, receipt
# processing skips OCR and the user enters fields manually.
OCR_ENABLED = "ocr_enabled"
# Investment price feed source (spec §27). Off by default — ``manual`` makes no
# network calls; ``stooq`` is keyless; ``alphavantage`` is a keyed provider
# (HAFI_INVESTMENT_API_KEY). Only ticker symbols are ever sent.
INVESTMENT_PRICE_SOURCE = "investment_price_source"
# Household default country for vendors with no country of their own — a fallback
# for the spend-by-location map, below a vendor's/txn's own country, above the
# currency guess. Empty = no default (geo behaviour unchanged). Never overwrites a
# manually-set country.
DEFAULT_VENDOR_COUNTRY = "default_vendor_country"
# Paperless-ngx base URL — editable in Settings → Integrations (non-secret), with
# the HAFI_PAPERLESS_URL env var as fallback. The token stays env-only (a secret;
# storing it in the DB needs at-rest encryption = deferred #15).
PAPERLESS_URL = "paperless_url"
# Energy-cost offset (HA): net HA solar/grid production against energy-bill spend.
# Off by default. `energy_source` picks how production is read; entities/topics are
# JSON-encoded lists; tariff is £/kWh (blank = derive from Home utility-meter logs);
# energy_category_id is which spend category counts as the energy bill.
ENERGY_SOURCE = "energy_source"  # off | ha_api | mqtt
ENERGY_PRODUCTION_ENTITIES = "energy_production_entities"  # JSON list of HA entity ids
ENERGY_PRODUCTION_TOPICS = "energy_production_topics"  # JSON list of MQTT topics
ENERGY_TARIFF_PER_KWH = "energy_tariff_per_kwh"  # decimal string, "" = derive
ENERGY_CATEGORY_ID = "energy_category_id"  # int string, "" = none
# How the production sensor reports, for the trend-over-time maths:
#   cumulative = an ever-increasing total (kWh) → per-period = diff between boundaries
#   interval   = production since the last reading → per-period = sum of readings
ENERGY_PRODUCTION_SEMANTICS = "energy_production_semantics"  # cumulative | interval

FX_MODES = {"manual", "frankfurter"}
ENERGY_SOURCES = {"off", "ha_api", "mqtt"}
ENERGY_SEMANTICS = {"cumulative", "interval"}
INVESTMENT_PRICE_SOURCES = {"manual", "stooq", "alphavantage"}
RECEIPT_MATCH_MODES = {"suggest", "auto"}

# Curated base-currency choices for the Settings dropdown (the top-10 world
# currencies by usage). The base currency is display-only — amounts are stored in
# their original currency; changing it just re-converts for display. (code, name,
# symbol); order = how they appear in the dropdown.
SUPPORTED_CURRENCIES: list[dict[str, str]] = [
    {"code": "GBP", "name": "British Pound", "symbol": "£"},
    {"code": "USD", "name": "US Dollar", "symbol": "$"},
    {"code": "EUR", "name": "Euro", "symbol": "€"},
    {"code": "JPY", "name": "Japanese Yen", "symbol": "¥"},
    {"code": "CNY", "name": "Chinese Yuan", "symbol": "¥"},
    {"code": "AUD", "name": "Australian Dollar", "symbol": "A$"},
    {"code": "CAD", "name": "Canadian Dollar", "symbol": "C$"},
    {"code": "CHF", "name": "Swiss Franc", "symbol": "CHF"},
    {"code": "HKD", "name": "Hong Kong Dollar", "symbol": "HK$"},
    {"code": "SGD", "name": "Singapore Dollar", "symbol": "S$"},
]
SUPPORTED_CURRENCY_CODES = {c["code"] for c in SUPPORTED_CURRENCIES}
PRIVACY_MODES = {"strict_local", "local_llm", "cloud_manual", "cloud_auto", "no_ai"}
AI_PROVIDERS = {"none", "openai_compatible"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}

# Backup-trim defaults: keep at most 90 days / 500 MB of safety backups, but never
# fewer than the most recent 3 regardless of age or size.
_BACKUP_TRIM_DEFAULTS = {"max_age_days": 90, "max_total_mb": 500, "min_keep": 3}

# Defaults that never vary at runtime — built once, not per call.
_STATIC_DEFAULTS: dict[str, str] = {
    FX_MODE: "manual",
    RECEIPT_MATCH_MODE: "suggest",
    AI_PROVIDER: "none",
    AI_BASE_URL: "",
    AI_MODEL: "",
    CLOUD_AI_PRIVACY_DEFAULT: "normal",
    INVESTMENT_PRICE_SOURCE: "manual",
    DEFAULT_VENDOR_COUNTRY: "",
    PAPERLESS_URL: "",
    ENERGY_SOURCE: "off",
    ENERGY_PRODUCTION_ENTITIES: "[]",
    ENERGY_PRODUCTION_TOPICS: "[]",
    ENERGY_TARIFF_PER_KWH: "",
    ENERGY_CATEGORY_ID: "",
    ENERGY_PRODUCTION_SEMANTICS: "cumulative",
}

# Memoised full defaults, keyed on the three env-derived values it depends on, so
# the dict is rebuilt only when the underlying config actually changes (e.g. a test
# overriding env_settings) rather than on every ``get``/``get_all`` call.
_defaults_key: tuple[str, str, str] | None = None
_defaults_cache: dict[str, str] = {}


def _defaults() -> dict[str, str]:
    """The full default map. Callers must NOT mutate the returned dict — it is a
    shared, cached instance (``get_all`` copies before it mutates)."""
    global _defaults_key, _defaults_cache
    key = (env_settings.currency, env_settings.privacy_mode.value, env_settings.log_level.upper())
    if key != _defaults_key:
        _defaults_cache = {
            **_STATIC_DEFAULTS,
            BASE_CURRENCY: key[0],
            PRIVACY_MODE: key[1],
            LOG_LEVEL: key[2],
        }
        _defaults_key = key
    return _defaults_cache


def get_privacy_mode(db: Session) -> str:
    mode = get(db, PRIVACY_MODE) or env_settings.privacy_mode.value
    return mode if mode in PRIVACY_MODES else "strict_local"


def get_log_level(db: Session) -> str:
    level = (get(db, LOG_LEVEL) or env_settings.log_level).upper()
    return level if level in LOG_LEVELS else "INFO"


def get_all(db: Session) -> dict[str, str]:
    values = dict(_defaults())  # copy: _defaults() is a shared cached instance
    for row in db.scalars(select(Setting)).all():
        if row.value is not None:
            values[row.key] = row.value
    return values


def get(db: Session, key: str) -> str | None:
    # one_or_none: ``key`` is unique (SR-2), so this is at most one row — and a
    # surprise duplicate raises loudly rather than silently shadowing a value.
    row = db.scalars(select(Setting).where(Setting.key == key)).one_or_none()
    if row is not None and row.value is not None:
        return row.value
    return _defaults().get(key)


def get_values(db: Session, keys: Iterable[str]) -> dict[str, str | None]:
    """Batch-read several settings in ONE query (``WHERE key IN (...)``) instead of a
    SELECT per key, for callers that need many at once. Returns ``{key: value}`` for
    every requested key, resolved exactly like :func:`get` — a stored non-null value
    wins, else the built-in default (or ``None`` if there is none). Duplicate keys and
    ordering don't matter."""
    wanted = list(dict.fromkeys(keys))  # de-duplicate, preserve first-seen order
    if not wanted:
        return {}
    stored = {
        row.key: row.value
        for row in db.scalars(select(Setting).where(Setting.key.in_(wanted))).all()
        if row.value is not None
    }
    defaults = _defaults()
    return {key: stored.get(key, defaults.get(key)) for key in wanted}


def _validate_entry(key: str, value: str | None) -> None:
    """Reject a malformed key/value before any write (see ``set_many``)."""
    if not isinstance(key, str) or not key or len(key) > 128:
        raise ValueError(f"invalid setting key: {key!r}")
    if value is not None and not isinstance(value, str):
        raise ValueError(f"invalid value for setting {key!r}: {value!r}")


def set_many(db: Session, values: dict[str, str]) -> None:
    """Atomically upsert several settings in ONE transaction, so a multi-field save
    is all-or-nothing.

    The whole batch is validated up front — a single bad key/value raises
    ``ValueError`` *before* anything is written — and a DB-level failure rolls the
    session back, so a failed batch never leaves a partial write. Existing rows are
    updated in place (keyed on ``key``, SR-2); unknown keys are inserted. Commits
    exactly once.
    """
    if not values:
        return
    for key, value in values.items():
        _validate_entry(key, value)
    try:
        for key, value in values.items():
            row = db.scalars(select(Setting).where(Setting.key == key)).one_or_none()
            if row is None:
                db.add(Setting(key=key, value=value))
            else:
                row.value = value
        db.commit()
    except Exception:
        db.rollback()
        raise


def set_value(db: Session, key: str, value: str) -> None:
    """Upsert a single setting and commit. Thin wrapper over the atomic
    :func:`set_many` primitive."""
    set_many(db, {key: value})


# --- Config-import allowlist (backlog CR-SEC-2) -----------------------------
#
# `backup_service.import_config` used to write ANY key/value from an uploaded JSON
# straight into the settings table, bypassing every `PUT /api/settings` validator.
# A malicious or careless export could then flip `privacy_mode` to a cloud mode,
# point `ai_base_url`/`paperless_url` at an internal host (SSRF), or pollute the
# table with arbitrary keys. We now accept ONLY this allowlist of side-effect-free,
# non-sensitive settings, each validated. Deliberately EXCLUDED:
#   - privacy_mode, ai_provider/ai_base_url/ai_model, paperless_url — security /
#     network / cloud vectors; must be set deliberately per instance, never bulk-
#     imported (validating the value doesn't make a valid `cloud_auto` safe to
#     silently apply, nor an internal URL safe — exclude them outright);
#   - base_currency — its re-conversion side-effect isn't run here (set it in
#     Settings, which recomputes);
#   - all secrets / internal state / infra keys (retention, backups, demo manifest,
#     MQTT/energy config, …).
# Each validator returns a normalised value, or None to reject (→ skipped).


def _imp_choice(allowed: set[str]):
    def check(value: str) -> str | None:
        v = str(value).strip()
        return v if v in allowed else None

    return check


def _imp_log_level(value: str) -> str | None:
    v = str(value).strip().upper()
    return v if v in LOG_LEVELS else None


def _imp_bool(value: str) -> str | None:
    v = str(value).strip().lower()
    return v if v in {"true", "false"} else None


def _imp_country(value: str) -> str | None:
    from app.services import geo

    v = str(value).strip().upper()
    if v == "":
        return ""  # clearing the default is allowed
    return v if (v != "EU" and v in geo.COUNTRY_NAMES) else None


IMPORTABLE_SETTINGS = {
    FX_MODE: _imp_choice(FX_MODES),
    RECEIPT_MATCH_MODE: _imp_choice(RECEIPT_MATCH_MODES),
    INVESTMENT_PRICE_SOURCE: _imp_choice(INVESTMENT_PRICE_SOURCES),
    DEFAULT_VENDOR_COUNTRY: _imp_country,
    LOG_LEVEL: _imp_log_level,
    OCR_ENABLED: _imp_bool,
}


def apply_imported_settings(db: Session, entries: list[dict]) -> dict:
    """Apply the ``settings`` section of a config import, accepting only allowlisted,
    validated keys (CR-SEC-2). Unknown, disallowed or invalid entries are skipped and
    reported. Upserts in place (household_id NULL, matching ``set_value``) and does
    NOT commit — it joins the caller's transaction."""
    set_count = 0
    skipped: set[str] = set()
    for entry in entries or []:
        key = entry.get("key")
        validator = IMPORTABLE_SETTINGS.get(key)
        if validator is None:
            if key:
                skipped.add(str(key))
            continue
        normalised = validator(entry.get("value") or "")
        if normalised is None:
            skipped.add(str(key))
            continue
        row = db.scalars(select(Setting).where(Setting.key == key)).one_or_none()
        if row is None:
            db.add(Setting(key=key, value=normalised))
        else:
            row.value = normalised
        set_count += 1
    return {"settings_set": set_count, "settings_skipped": len(skipped), "skipped_setting_keys": sorted(skipped)}


def get_base_currency(db: Session) -> str:
    return (get(db, BASE_CURRENCY) or env_settings.currency).upper()


def get_fx_mode(db: Session) -> str:
    mode = get(db, FX_MODE) or "manual"
    return mode if mode in FX_MODES else "manual"


def get_receipt_delete_after_processing(db: Session) -> bool:
    """Whether a receipt's original file is dropped once it's processed & matched
    (backlog #147). **Off by default** — a finance app should keep receipts so they
    stay viewable; only an explicit ``"true"`` (Settings → retention) enables the
    privacy/disk-saving drop."""
    return get(db, RECEIPT_DELETE_AFTER_PROCESSING) == "true"


def get_investment_price_source(db: Session) -> str:
    """The configured investment price feed (spec §27). Defaults to ``manual``
    (no network); an unknown stored value falls back to ``manual``."""
    source = get(db, INVESTMENT_PRICE_SOURCE) or "manual"
    return source if source in INVESTMENT_PRICE_SOURCES else "manual"


def get_ocr_enabled(db: Session) -> bool:
    """Whether receipt OCR is allowed to run (Settings → Services). On by default;
    only an explicit ``"false"`` disables it (then receipts are entered manually)."""
    return get(db, OCR_ENABLED) != "false"


def get_paperless_url(db: Session) -> str:
    """The Paperless-ngx base URL: the Settings value if set, else the
    HAFI_PAPERLESS_URL env var (fallback), else "". The token is never stored
    here — it stays env-only (a secret)."""
    stored = (get(db, PAPERLESS_URL) or "").strip()
    return stored or (env_settings.paperless_url or "").strip()


def get_default_vendor_country(db: Session) -> str | None:
    """The household default country (ISO-3166-1 alpha-2) for vendors with no
    country of their own. Empty/unset means no default. Used as a fallback in
    ``geo.country_for`` — below a vendor's/txn's own country, above the currency
    guess — so it never overrides a manually-set country."""
    code = (get(db, DEFAULT_VENDOR_COUNTRY) or "").strip().upper()
    return code or None


def get_mqtt_publish_selection(db: Session) -> dict:
    """Disabled MQTT sensor groups + individual sensor keys (backlog: choose what to
    publish). Returns ``{"groups": [...], "sensors": [...]}``; empty lists (the
    default) mean publish everything. Tolerates a missing/garbled value."""
    raw = get(db, MQTT_PUBLISH_SELECTION)
    if not raw:
        return {"groups": [], "sensors": []}
    try:
        data = json.loads(raw)
        return {
            "groups": [str(g) for g in data.get("groups", [])],
            "sensors": [str(s) for s in data.get("sensors", [])],
        }
    except (ValueError, TypeError):  # pragma: no cover - defensive against bad data
        return {"groups": [], "sensors": []}


def set_mqtt_publish_selection(db: Session, *, groups: list[str], sensors: list[str]) -> None:
    set_value(
        db,
        MQTT_PUBLISH_SELECTION,
        json.dumps({"groups": sorted(set(groups)), "sensors": sorted(set(sensors))}),
    )


def _int_setting(db: Session, key: str, default: int) -> int:
    raw = get(db, key)
    if raw is None:
        return default
    try:
        return max(0, int(raw))
    except (ValueError, TypeError):
        return default


def get_backup_trim_policy(db: Session) -> dict:
    """Resolved backup-trim limits (backlog #78), falling back to the defaults."""
    return {
        "max_age_days": _int_setting(db, BACKUP_MAX_AGE_DAYS, _BACKUP_TRIM_DEFAULTS["max_age_days"]),
        "max_total_mb": _int_setting(db, BACKUP_MAX_TOTAL_MB, _BACKUP_TRIM_DEFAULTS["max_total_mb"]),
        "min_keep": _int_setting(db, BACKUP_MIN_KEEP, _BACKUP_TRIM_DEFAULTS["min_keep"]),
    }
