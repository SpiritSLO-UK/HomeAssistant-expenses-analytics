"""Tests for the redaction utility (spec §22.4, §28.3; backlog #7, #13)."""

from __future__ import annotations

import re

import pytest

from app.services.redaction import (
    contains_sensitive,
    redact_for_cloud,
    redact_text,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Card 4111 1111 1111 1111 used", "Card [card] 1111 used"),
        ("PAN 4111111111111111", "PAN [card] 1111"),
        ("sort 12-34-56", "sort [sort-code]"),
        ("acc 12345678 end", "acc [account] end"),
        ("IBAN GB29NWBK60161331926819 here", "IBAN [iban] here"),
        ("lives at SW1A 1AA today", "lives at [postcode] today"),
        ("mail me a.b+x@example.co.uk now", "mail me [email] now"),
    ],
)
def test_redact_text(raw, expected):
    assert redact_text(raw) == expected


def test_normal_merchant_text_unchanged():
    # Store numbers (3-4 digits) and dates must NOT be redacted.
    text = "TESCO STORES 3142 DARTFORD 02/05/2026"
    assert redact_text(text) == text
    assert contains_sensitive(text) is False


def test_contains_sensitive():
    assert contains_sensitive("acc 12345678") is True
    assert contains_sensitive("COSTA COFFEE 482") is False
    assert contains_sensitive("") is False


def test_redact_for_cloud_drops_extra_fields_and_redacts():
    payload = {
        "description": "PAYMENT FROM 12-34-56 acct 87654321 ref a@b.com",
        "amount": -38.99,
        "currency": "GBP",
        "candidate_categories": ["DIY", "Home"],
        # Fields that must never leave the device:
        "account_id": 7,
        "merchant_raw": "secret",
        "source_hash": "deadbeef",
    }
    out = redact_for_cloud(payload)
    assert set(out.keys()) == {"description", "amount", "currency", "candidate_categories"}
    assert "12-34-56" not in out["description"]
    assert "87654321" not in out["description"]
    assert "a@b.com" not in out["description"]
    assert out["amount"] == -38.99
    assert out["candidate_categories"] == ["DIY", "Home"]


def test_redact_for_cloud_handles_missing_fields():
    assert redact_for_cloud({"amount": -1.0}) == {"amount": -1.0}


def test_candidate_category_with_pii_is_masked():
    # A category renamed to contain PII must not leak via the list field.
    payload = {
        "description": "shop",
        "candidate_categories": ["Groceries", "call me on +44 20 7946 0958"],
    }
    out = redact_for_cloud(payload)
    assert out["candidate_categories"][0] == "Groceries"
    assert "+44 20 7946 0958" not in out["candidate_categories"][1]
    assert "[phone]" in out["candidate_categories"][1]


def test_candidate_category_email_and_account_masked():
    payload = {"candidate_categories": ["mail bob@example.com acct 87654321"]}
    out = redact_for_cloud(payload)
    masked = out["candidate_categories"][0]
    assert "bob@example.com" not in masked
    assert "87654321" not in masked
    assert "[email]" in masked
    assert "[account]" in masked


@pytest.mark.parametrize(
    "raw",
    [
        "call +44 20 7946 0958 now",
        "phone (020) 7946 0958",
        "US +1 (415) 555-2671 line",
    ],
)
def test_international_phone_masked(raw):
    out = redact_text(raw)
    assert "[phone]" in out
    # No long digit run should survive.
    assert not re.search(r"\d{7}", out)


def test_international_number_always_masked_even_if_card_shaped():
    # A "0044…"-prefixed number happens to be 16 digits so it masks as [card];
    # the point is that no raw digit run leaks, regardless of which rule fires.
    out = redact_text("ring 0044 20 7946 0958")
    assert ("[phone]" in out) or ("[card]" in out)
    assert "0044 20 7946" not in out


def test_long_account_like_run_masked():
    # 8+ digit standalone runs that are NOT card-length are masked as accounts;
    # short store/reference numbers are left intact.
    assert "[account]" in redact_text("acct 87654321 done")  # 8 digits
    assert "[account]" in redact_text("ref 1234567890 done")  # 10-digit non-card run
    assert redact_text("STORE 3142") == "STORE 3142"
    assert redact_text("COSTA 482") == "COSTA 482"


def test_contains_sensitive_bounds_and_behaviour():
    assert contains_sensitive("acct 87654321") is True
    assert contains_sensitive("call +44 20 7946 0958") is True
    assert contains_sensitive("TESCO STORES 3142 DARTFORD") is False
    # Pathological long input is truncated and does not hang.
    assert contains_sensitive("a" * 20000) is False
