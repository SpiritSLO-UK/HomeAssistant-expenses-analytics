"""User-editable settings, persisted in the ``settings`` table (spec §38).

Bootstrap/config defaults still come from environment variables (app.config);
these are the runtime-editable knobs surfaced in the Settings UI. Stored as
simple key/value strings.
"""

from __future__ import annotations

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
RECEIPT_DELETE_AFTER_PROCESSING = "receipt_delete_after_processing"  # default true
# Backup-trim policy (bound disk growth — backlog #78): how long / how large the
# safety-backup history may grow before old snapshots are pruned.
BACKUP_MAX_AGE_DAYS = "backup_max_age_days"
BACKUP_MAX_TOTAL_MB = "backup_max_total_mb"
BACKUP_MIN_KEEP = "backup_min_keep"
# Runtime log level, editable from Settings (spec §38). Mirrors the env default.
LOG_LEVEL = "log_level"
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


def _defaults() -> dict[str, str]:
    return {
        BASE_CURRENCY: env_settings.currency,
        FX_MODE: "manual",
        RECEIPT_MATCH_MODE: "suggest",
        PRIVACY_MODE: env_settings.privacy_mode.value,
        AI_PROVIDER: "none",
        AI_BASE_URL: "",
        AI_MODEL: "",
        LOG_LEVEL: env_settings.log_level.upper(),
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


def get_privacy_mode(db: Session) -> str:
    mode = get(db, PRIVACY_MODE) or env_settings.privacy_mode.value
    return mode if mode in PRIVACY_MODES else "strict_local"


def get_log_level(db: Session) -> str:
    level = (get(db, LOG_LEVEL) or env_settings.log_level).upper()
    return level if level in LOG_LEVELS else "INFO"


def get_all(db: Session) -> dict[str, str]:
    values = _defaults()
    for row in db.scalars(select(Setting)).all():
        if row.value is not None:
            values[row.key] = row.value
    return values


def get(db: Session, key: str) -> str | None:
    row = db.scalars(select(Setting).where(Setting.key == key)).first()
    if row is not None and row.value is not None:
        return row.value
    return _defaults().get(key)


def set_value(db: Session, key: str, value: str) -> None:
    row = db.scalars(select(Setting).where(Setting.key == key)).first()
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def get_base_currency(db: Session) -> str:
    return (get(db, BASE_CURRENCY) or env_settings.currency).upper()


def get_fx_mode(db: Session) -> str:
    mode = get(db, FX_MODE) or "manual"
    return mode if mode in FX_MODES else "manual"


def get_receipt_delete_after_processing(db: Session) -> bool:
    """Whether a receipt's original file is dropped once it's processed & matched
    (backlog #147). On by default — only an explicit ``"false"`` turns it off."""
    return get(db, RECEIPT_DELETE_AFTER_PROCESSING) != "false"


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
