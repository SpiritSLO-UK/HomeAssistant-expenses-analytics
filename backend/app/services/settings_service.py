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

FX_MODES = {"manual", "frankfurter"}


def _defaults() -> dict[str, str]:
    return {
        BASE_CURRENCY: env_settings.currency,
        FX_MODE: "manual",
    }


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
