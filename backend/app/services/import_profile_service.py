"""Saved CSV import profiles + CSV inspection for the column-mapping UI.

A profile is just a named ``{logical_field: csv_header}`` mapping (+ a default
currency), so an unsupported bank's statement imports in one click next time and
the mapping can be exported/shared (it holds no transaction data).
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ImportProfile
from app.parsers.base import read_csv_rows
from app.parsers.generic_csv import LOGICAL_FIELDS, suggest_mapping
from app.services.household_service import get_or_create_default_household

_SAMPLE_ROWS = 5
_VALID_FIELDS = {f["key"] for f in LOGICAL_FIELDS}
_VALID_DATE_FORMATS = {"auto", "dmy", "mdy"}


def _clean_date_format(value: str | None) -> str:
    """Normalise a profile date_format to one of the allowed values, defaulting
    unknown/empty to "auto" (the historic per-file heuristic)."""
    v = (value or "auto").strip().lower()
    return v if v in _VALID_DATE_FORMATS else "auto"


def inspect_csv(content: bytes) -> dict:
    """Headers + a few sample rows + a suggested column mapping, to drive the
    Import column-mapping UI. Raises ValueError if it isn't a usable CSV."""
    headers, rows = read_csv_rows(content)
    headers = [h for h in headers if h]
    if not headers:
        raise ValueError("No columns found — is this a CSV with a header row?")
    return {
        "headers": headers,
        "sample_rows": rows[:_SAMPLE_ROWS],
        "suggested_mapping": suggest_mapping(headers),
        "fields": LOGICAL_FIELDS,
    }


def _clean_mapping(mapping: dict[str, str] | None) -> dict[str, str]:
    """Keep only known logical fields whose header is a non-empty string."""
    return {k: str(v).strip() for k, v in (mapping or {}).items() if k in _VALID_FIELDS and str(v).strip()}


def _to_out(p: ImportProfile) -> dict:
    try:
        mapping = json.loads(p.mapping_json)
    except (ValueError, TypeError):  # pragma: no cover - defensive against bad data
        mapping = {}
    return {
        "id": p.id,
        "name": p.name,
        "mapping": mapping,
        "default_currency": p.default_currency,
        "date_format": _clean_date_format(getattr(p, "date_format", "auto")),
    }


def list_profiles(db: Session) -> list[dict]:
    rows = db.scalars(select(ImportProfile).order_by(ImportProfile.name)).all()
    return [_to_out(p) for p in rows]


def create_profile(
    db: Session, *, name: str, mapping: dict[str, str],
    default_currency: str = "GBP", date_format: str = "auto",
) -> dict:
    household = get_or_create_default_household(db)
    name = (name or "").strip()
    if not name:
        raise ValueError("A profile name is required.")
    if db.scalars(select(ImportProfile).where(ImportProfile.name == name)).first() is not None:
        raise ValueError(f"An import profile named {name!r} already exists.")
    profile = ImportProfile(
        household_id=household.id,
        name=name,
        mapping_json=json.dumps(_clean_mapping(mapping)),
        default_currency=(default_currency or "GBP").strip().upper(),
        date_format=_clean_date_format(date_format),
    )
    db.add(profile)
    db.commit()
    return _to_out(profile)


def update_profile(
    db: Session,
    profile_id: int,
    *,
    name: str | None = None,
    mapping: dict[str, str] | None = None,
    default_currency: str | None = None,
    date_format: str | None = None,
) -> dict | None:
    profile = db.get(ImportProfile, profile_id)
    if profile is None:
        return None
    if name is not None and name.strip():
        profile.name = name.strip()
    if mapping is not None:
        profile.mapping_json = json.dumps(_clean_mapping(mapping))
    if default_currency is not None and default_currency.strip():
        profile.default_currency = default_currency.strip().upper()
    if date_format is not None:
        profile.date_format = _clean_date_format(date_format)
    db.commit()
    return _to_out(profile)


def delete_profile(db: Session, profile_id: int) -> bool:
    profile = db.get(ImportProfile, profile_id)
    if profile is None:
        return False
    db.delete(profile)
    db.commit()
    return True
