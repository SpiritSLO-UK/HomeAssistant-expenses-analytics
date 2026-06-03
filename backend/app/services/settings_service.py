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

FX_MODES = {"manual", "frankfurter"}
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
