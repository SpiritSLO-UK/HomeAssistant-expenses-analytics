"""Tests for the redaction utility (spec §22.4, §28.3; backlog #7, #13)."""

from __future__ import annotations

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
