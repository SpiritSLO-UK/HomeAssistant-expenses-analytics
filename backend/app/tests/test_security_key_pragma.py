"""Unit tests for safe SQLCipher key-PRAGMA construction (CR-SEC-15).

These are driver-independent: they don't need the ``sqlcipher3`` wheel (absent on
Windows). Correctness of the escaping is proven with the stdlib ``sqlite3`` module
— ``SELECT <literal>`` must decode back to the exact original passphrase, which
holds for any well-formed SQLite string literal. The real encrypt/unlock
round-trip lives in ``test_atrest_encryption.py`` and runs on Linux/CI.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.services import security_service as ss

# Passphrases that would break naive interpolation: quotes, doubled quotes,
# backslashes, a leading/trailing quote, unicode, and SQL-injection-shaped text.
TRICKY = [
    "hunter2",
    "",
    "it's a secret",
    "''",
    "'; DROP TABLE users; --",
    "quote' and more ' quotes",
    r"back\slash",
    "quote at end'",
    "'quote at start",
    'double "quotes"',
    "pȧss wörd 🔐",
    "tab\tand\nnewline",
]


def _decode_literal(literal: str) -> str:
    """Ask SQLite itself what string the literal denotes (no SQLCipher needed)."""
    con = sqlite3.connect(":memory:")
    try:
        (value,) = con.execute(f"SELECT {literal}").fetchone()
        return value
    finally:
        con.close()


@pytest.mark.parametrize("passphrase", TRICKY)
def test_literal_roundtrips_through_sqlite(passphrase):
    literal = ss._sql_string_literal(passphrase)
    # Well-formed, single-quoted literal.
    assert literal.startswith("'") and literal.endswith("'")
    # SQLite decodes it back to exactly the input — proves escaping is correct.
    assert _decode_literal(literal) == passphrase


@pytest.mark.parametrize("passphrase", TRICKY)
def test_single_quotes_are_doubled(passphrase):
    literal = ss._sql_string_literal(passphrase)
    # Every embedded quote is doubled: no lone single quote inside the body.
    body = literal[1:-1]
    assert body == passphrase.replace("'", "''")


def test_key_pragma_shape():
    pragma = ss._key_pragma("it's fine")
    assert pragma == "PRAGMA key = 'it''s fine'"
    # Statement round-trips: strip the prefix, SQLite decodes the literal.
    literal = pragma[len("PRAGMA key = ") :]
    assert _decode_literal(literal) == "it's fine"


def test_nul_byte_is_rejected():
    # A NUL would be truncated by the driver's C string handling, silently
    # shortening the passphrase — reject it instead.
    with pytest.raises(ValueError):
        ss._sql_string_literal("bad\x00key")
    with pytest.raises(ValueError):
        ss._key_pragma("bad\x00key")


def test_no_raw_passphrase_interpolation_leaks_unescaped_quote():
    # Regression guard for the original bug: the produced literal must not
    # contain an unescaped (odd-count) single quote sequence anywhere in the body.
    literal = ss._sql_string_literal("a'b'c")
    body = literal[1:-1]
    # Splitting on the doubled-quote token must leave no stray single quotes.
    assert "'" not in body.replace("''", "")
