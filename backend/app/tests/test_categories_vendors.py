"""Tests for categories, vendors, auto-categorisation and the dashboard (Stage 2).

Covers spec §29 Stage 2 acceptance: user can categorise transactions, vendor
aliases work, and the dashboard groups by category.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from app.models import Transaction
from app.services import category_service, vendor_service

# A fixed month matching the sample CSV data, so dashboard tests are
# independent of the real system clock.
MONTH = "2026-05-15"


@pytest.fixture(autouse=True)
def _reset_category_map_cache():
    """The library_id -> id map is cached process-globally (SR-A2). Clear it around
    every test so a map built against one throwaway test DB can never leak into the
    next, independent of the automatic version-signal invalidation."""
    category_service.invalidate_category_map_cache()
    yield
    category_service.invalidate_category_map_cache()


def _import_curve(client, samples_dir):
    content = (samples_dir / "curve-sample.csv").read_bytes()
    up = client.post(
        "/api/imports/upload",
        files={"file": ("curve-sample.csv", content, "text/csv")},
    ).json()
    client.post(f"/api/imports/{up['import_id']}/confirm")
    return up


def _category_id(client, name: str) -> int:
    cats = client.get("/api/categories").json()
    return next(c["id"] for c in cats if c["name"] == name)


# --- library import ---

def test_library_seeded_on_startup(client):
    cats = client.get("/api/categories").json()
    assert len(cats) == 23
    names = {c["name"] for c in cats}
    assert {"Groceries", "DIY", "Subscriptions", "Income", "Cashback"} <= names


def test_import_library_idempotent(db):
    first = category_service.import_library(db)
    second = category_service.import_library(db)
    assert first == 23
    assert second == 0  # nothing new on re-import
    assert len(category_service.list_categories(db)) == 23


def test_update_category_ignores_unknown_fields_and_syncs_path(db):
    # A rename keeps the mirrored path in sync; managed columns can't be set via
    # a blind setattr (SR-A2).
    cat = category_service.create_category(db, {"name": "Coffee"})
    assert cat.path == "Coffee" and cat.is_system is False
    updated = category_service.update_category(
        db, cat.id, {"name": "Cafe", "is_system": True, "household_id": 999}
    )
    assert updated.name == "Cafe"
    assert updated.path == "Cafe"        # path mirrored the old name → synced
    assert updated.is_system is False    # protected field unchanged


# --- vendor recommendation: create + link from a transaction (suggest & confirm) ---

def test_create_vendor_from_transaction_derives_signature(client, samples_dir):
    _import_curve(client, samples_dir)
    t = next(t for t in client.get("/api/transactions").json()["items"] if t["merchant_id"] is None)

    res = client.post(f"/api/transactions/{t['id']}/create-vendor", json={})
    assert res.status_code == 200, res.text
    vid = res.json()["merchant_id"]
    assert vid is not None  # the transaction is now linked

    vendors = client.get("/api/vendors").json()
    vendor = next(v for v in vendors if v["id"] == vid)
    # Named from the OCR/parsed merchant signature (non-empty, drops trailing digits).
    assert vendor["canonical_name"]
    assert not any(ch.isdigit() for ch in vendor["canonical_name"])


def test_create_vendor_from_transaction_explicit_name(client, samples_dir):
    _import_curve(client, samples_dir)
    t = next(t for t in client.get("/api/transactions").json()["items"] if t["merchant_id"] is None)

    res = client.post(f"/api/transactions/{t['id']}/create-vendor", json={"name": "My Corner Shop"})
    assert res.status_code == 200, res.text
    vid = res.json()["merchant_id"]
    vendors = client.get("/api/vendors").json()
    assert next(v for v in vendors if v["id"] == vid)["canonical_name"] == "My Corner Shop"


def test_create_vendor_from_transaction_404(client):
    assert client.post("/api/transactions/999999/create-vendor", json={}).status_code == 404


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BP", "BP"),                     # short acronym preserved (not "Bp")
        ("HSBC", "HSBC"),                 # 4-char acronym preserved (not "Hsbc")
        ("EE", "EE"),                     # 2-char brand preserved (not "Ee")
        ("O2", "O2"),                     # digit-bearing brand preserved
        ("M&S", "M&S"),                   # ampersand brand preserved (not "M&s")
        ("TESCO STORES", "Tesco Stores"), # genuine all-caps words still title-cased
        ("BP CONNECT", "BP Connect"),     # acronym + word in one signature
        ("Costa Coffee", "Costa Coffee"), # already mixed-case → left untouched
    ],
)
def test_create_vendor_normalises_acronyms(db, raw, expected):
    """Naming an all-caps merchant no longer mangles acronyms/brands via a naive
    ``str.title()`` (mirrors the FE fix in #393)."""
    txn = Transaction(
        description_raw=raw,
        merchant_raw=raw,
        amount="1.00",
        direction="debit",
        transaction_date=date(2026, 5, 15),
    )
    db.add(txn)
    db.flush()
    vendor = vendor_service.create_from_transaction(db, txn)
    assert vendor.canonical_name == expected


@pytest.mark.parametrize(
    ("merchant_raw", "expected"),
    [("BP", "BP"), ("HSBC", "HSBC"), ("M&S", "M&S"), ("TESCO STORES", "Tesco Stores")],
)
def test_learn_vendor_category_normalises_acronyms(db, merchant_raw, expected):
    """Manual-correction learning normalises the canonical name the same way."""
    cat = category_service.create_category(db, {"name": "Groceries"})
    vendor = vendor_service.learn_vendor_category(db, "SOME DESCRIPTION 42", merchant_raw, cat.id)
    assert vendor.canonical_name == expected


# --- category CRUD ---

def test_category_crud(client):
    created = client.post("/api/categories", json={"name": "Childcare", "colour": "#FF8800"})
    assert created.status_code == 201
    cid = created.json()["id"]

    patched = client.patch(f"/api/categories/{cid}", json={"name": "Kids"})
    assert patched.json()["name"] == "Kids"

    assert client.delete(f"/api/categories/{cid}").status_code == 204
    assert client.get(f"/api/categories/{cid}").status_code == 404


def test_create_without_colour_gets_unused_palette_colour(db):
    """A create with no colour still yields a non-null colour that isn't already
    used by an existing category (backlog: random non-repeating colour)."""
    existing = {"#EF5350", "#42A5F5", "#66BB6A"}
    for i, hexval in enumerate(existing):
        category_service.create_category(db, {"name": f"Seed{i}", "colour": hexval})

    created = category_service.create_category(db, {"name": "NoColour"})
    assert created.colour is not None
    assert created.colour != ""
    # Distinct from every colour already assigned (case-insensitive).
    used = {c.upper() for c in existing}
    assert created.colour.upper() not in used
    # And it came from the shared palette (palette not yet exhausted here).
    assert created.colour in category_service.DEFAULT_COLOUR_PALETTE


def test_create_without_colour_falls_back_when_palette_exhausted(db):
    """Once every palette colour is taken, a create still gets a non-null,
    well-formed hex rather than a repeat or a null."""
    for i, hexval in enumerate(category_service.DEFAULT_COLOUR_PALETTE):
        category_service.create_category(db, {"name": f"Pal{i}", "colour": hexval})

    created = category_service.create_category(db, {"name": "Overflow"})
    assert created.colour is not None
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", created.colour)


def test_create_with_empty_colour_string_gets_default(db):
    """An explicit empty-string colour is treated as 'no colour' and defaulted."""
    created = category_service.create_category(db, {"name": "Blank", "colour": ""})
    assert created.colour
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", created.colour)


def test_system_category_can_be_deleted(client):
    """Built-in (system) categories are deletable too (backlog: full control)."""
    income_id = _category_id(client, "Income")
    assert client.delete(f"/api/categories/{income_id}").status_code == 204
    assert client.get(f"/api/categories/{income_id}").status_code == 404


def test_category_merge_reassigns_references(db):
    """Merging re-points every reference (transactions, budgets, …) from the source
    to the target, then deletes the source."""
    from datetime import date
    from decimal import Decimal

    from app.models import Account, Budget, Transaction

    coffee = category_service.create_category(db, {"name": "Coffee"})
    eating = category_service.create_category(db, {"name": "Eating Out"})
    acct = Account(name="A", account_type="current_account", currency="GBP")
    db.add(acct)
    db.flush()
    txn = Transaction(
        account_id=acct.id, transaction_date=date(2026, 5, 15), description_raw="LATTE",
        amount=Decimal("-3.50"), currency="GBP", direction="debit",
        base_amount=Decimal("-3.50"), fx_rate=Decimal("1"), category_id=coffee.id,
    )
    budget = Budget(name="Coffee budget", period="monthly", amount=Decimal("20"), category_id=coffee.id)
    db.add_all([txn, budget])
    db.commit()

    target = category_service.merge_category(db, coffee.id, eating.id)
    assert target.id == eating.id
    assert category_service.get_category(db, coffee.id) is None  # source removed
    db.refresh(txn)
    db.refresh(budget)
    assert txn.category_id == eating.id
    assert budget.category_id == eating.id


def test_category_merge_repoints_subscriptions(db):
    """#12: merging re-points a subscription's ``category_id`` to the target rather
    than letting the source-delete NULL it (FK ondelete=SET NULL); subscriptions in
    other categories are left untouched."""
    from decimal import Decimal

    from app.models import Subscription

    source = category_service.create_category(db, {"name": "Streaming"})
    target = category_service.create_category(db, {"name": "Subscriptions"})
    other = category_service.create_category(db, {"name": "Utilities"})
    moved = Subscription(name="Netflix", amount=Decimal("10.99"), category_id=source.id)
    untouched = Subscription(name="Water", amount=Decimal("30.00"), category_id=other.id)
    db.add_all([moved, untouched])
    db.commit()

    category_service.merge_category(db, source.id, target.id)
    db.refresh(moved)
    db.refresh(untouched)
    assert moved.category_id == target.id       # re-pointed, not NULLed
    assert untouched.category_id == other.id     # unrelated subscription unaffected


def test_category_merge_repoints_category_equals_rule_conditions(db):
    """SR-4: merging also re-points rules that *match on* the source category
    (`category_equals`), not just rules that *set* it — otherwise those rules would
    silently stop matching once the source category is deleted."""
    from app.models import Rule

    coffee = category_service.create_category(db, {"name": "Coffee"})
    eating = category_service.create_category(db, {"name": "Eating Out"})
    sets = Rule(name="set", condition_type="merchant_contains", condition_value="cafe",
                action_type="set_category", action_value=str(coffee.id))
    matches = Rule(name="match", condition_type="category_equals", condition_value=str(coffee.id),
                   action_type="require_review", action_value=None)
    db.add_all([sets, matches])
    db.commit()

    category_service.merge_category(db, coffee.id, eating.id)
    db.refresh(sets)
    db.refresh(matches)
    assert sets.action_value == str(eating.id)        # the set_category action (already worked)
    assert matches.condition_value == str(eating.id)  # the category_equals condition (SR-4 fix)


def test_category_merge_either_direction_preserves_all_links(db):
    """Merge direction: the survivor is whichever category is passed as the target,
    and every reference type re-points onto it in *both* directions (keep-A-remove-B
    and keep-B-remove-A) with nothing dropped. Backs the frontend "Keep" toggle,
    which merely swaps which id is source vs target."""
    from decimal import Decimal

    from app.models import Rule, Subscription, Vendor

    def _link_all(cat_id: int) -> dict:
        """Attach one of every re-pointed reference type to ``cat_id``."""
        sub = Subscription(name=f"sub-{cat_id}", amount=Decimal("9.99"), category_id=cat_id)
        vendor = Vendor(canonical_name=f"vend-{cat_id}", default_category_id=cat_id)
        child = category_service.create_category(db, {"name": f"child-{cat_id}"})
        child.parent_id = cat_id
        sets = Rule(name=f"set-{cat_id}", condition_type="merchant_contains", condition_value="x",
                    action_type="set_category", action_value=str(cat_id))
        matches = Rule(name=f"match-{cat_id}", condition_type="category_equals",
                       condition_value=str(cat_id), action_type="require_review", action_value=None)
        db.add_all([sub, vendor, sets, matches])
        db.commit()
        return {"sub": sub, "vendor": vendor, "child": child, "sets": sets, "matches": matches}

    def _assert_repointed(links: dict, survivor_id: int) -> None:
        for obj in links.values():
            db.refresh(obj)
        assert links["sub"].category_id == survivor_id
        assert links["vendor"].default_category_id == survivor_id
        assert links["child"].parent_id == survivor_id
        assert links["sets"].action_value == str(survivor_id)
        assert links["matches"].condition_value == str(survivor_id)

    # Direction 1: keep B, remove A (links live on the removed A).
    a = category_service.create_category(db, {"name": "Alpha"})
    b = category_service.create_category(db, {"name": "Beta"})
    links_a = _link_all(a.id)
    survivor = category_service.merge_category(db, a.id, b.id)
    assert survivor.id == b.id
    assert category_service.get_category(db, a.id) is None
    _assert_repointed(links_a, b.id)

    # Direction 2: keep C, remove D — same call, opposite survivor (links on removed D).
    c = category_service.create_category(db, {"name": "Gamma"})
    d = category_service.create_category(db, {"name": "Delta"})
    links_d = _link_all(d.id)
    survivor2 = category_service.merge_category(db, d.id, c.id)
    assert survivor2.id == c.id
    assert category_service.get_category(db, d.id) is None
    _assert_repointed(links_d, c.id)


def test_merge_vendor_repoints_txns_and_moves_dedupes_aliases(db):
    """SR-A3: merging re-points a transaction's ``merchant_id`` to the target,
    moves the source's aliases onto the target while dropping exact-duplicate
    alias strings (case-insensitive), and deletes the source vendor."""
    from datetime import date
    from decimal import Decimal

    from sqlalchemy import select

    from app.models import Account, Transaction, VendorAlias

    source = vendor_service.create_vendor(
        db, {"canonical_name": "Tesco Metro", "alias": "TESCO METRO", "match_type": "contains"}
    )
    target = vendor_service.create_vendor(
        db, {"canonical_name": "Tesco", "alias": "TESCO", "match_type": "contains"}
    )
    # A duplicate alias string (differing only in case) on the source — must be dropped.
    vendor_service.add_alias(db, source.id, "tesco", "contains")

    acct = Account(name="A", account_type="current_account", currency="GBP")
    db.add(acct)
    db.flush()
    txn = Transaction(
        account_id=acct.id, transaction_date=date(2026, 5, 15), description_raw="TESCO METRO 12",
        amount=Decimal("-3.50"), currency="GBP", direction="debit",
        base_amount=Decimal("-3.50"), fx_rate=Decimal("1"), merchant_id=source.id,
    )
    db.add(txn)
    db.commit()

    result = vendor_service.merge_vendor(db, source.id, target.id)
    assert result.id == target.id
    assert vendor_service.get_vendor(db, source.id) is None  # source removed

    db.refresh(txn)
    assert txn.merchant_id == target.id  # transaction re-pointed

    # Target keeps its own "TESCO", gains the unique "TESCO METRO", and the
    # case-duplicate "tesco" is dropped (not moved).
    aliases = {a.alias.lower() for a in db.scalars(
        select(VendorAlias).where(VendorAlias.vendor_id == target.id)
    ).all()}
    assert aliases == {"tesco", "tesco metro"}


def test_default_category_from_linked_vendor_not_matched(db):
    """#7: when a rule has already linked vendor B but the description alias matches
    vendor A, the txn keeps B and is categorised with B's default — never A's."""
    from datetime import date
    from decimal import Decimal

    cat_a = category_service.create_category(db, {"name": "CatA"})
    cat_b = category_service.create_category(db, {"name": "CatB"})
    vendor_service.create_vendor(
        db, {"canonical_name": "VendorA", "alias": "ALPHA", "match_type": "contains",
             "default_category_id": cat_a.id},
    )
    vendor_b = vendor_service.create_vendor(
        db, {"canonical_name": "VendorB", "default_category_id": cat_b.id},
    )
    # A rule ran first and linked vendor B; the description still matches A's alias.
    txn = Transaction(
        description_raw="ALPHA STORE 12", amount=Decimal("-5.00"), direction="debit",
        currency="GBP", transaction_date=date(2026, 5, 15), merchant_id=vendor_b.id,
    )
    db.add(txn)
    db.flush()

    assert vendor_service.normalise_transaction(db, txn) is True
    assert txn.merchant_id == vendor_b.id   # linked vendor preserved (not overwritten by A)
    assert txn.category_id == cat_b.id      # B's default applied, not A's (#7)


def test_default_category_from_matched_vendor_when_unlinked(db):
    """Control for #7: with no vendor pre-linked, the matched vendor's own default is
    applied as before (the fix only changes the pre-linked case)."""
    from datetime import date
    from decimal import Decimal

    cat_a = category_service.create_category(db, {"name": "SoloCat"})
    vendor_a = vendor_service.create_vendor(
        db, {"canonical_name": "SoloVendor", "alias": "GAMMA", "match_type": "contains",
             "default_category_id": cat_a.id},
    )
    txn = Transaction(
        description_raw="GAMMA MART 9", amount=Decimal("-5.00"), direction="debit",
        currency="GBP", transaction_date=date(2026, 5, 15),
    )
    db.add(txn)
    db.flush()

    assert vendor_service.normalise_transaction(db, txn) is True
    assert txn.merchant_id == vendor_a.id
    assert txn.category_id == cat_a.id


def test_unknown_default_category_returns_400_not_500(client):
    """#10: an unknown default_category_id is rejected with 400 (not an opaque 500
    from the FK IntegrityError) on create, update and set-default-category."""
    assert client.post(
        "/api/vendors", json={"canonical_name": "V", "default_category_id": 999_999}
    ).status_code == 400

    vid = client.post("/api/vendors", json={"canonical_name": "W"}).json()["id"]
    assert client.patch(
        f"/api/vendors/{vid}", json={"default_category_id": 999_999}
    ).status_code == 400
    assert client.post(
        f"/api/vendors/{vid}/set-default-category", json={"category_id": 999_999}
    ).status_code == 400

    # A valid category still succeeds; clearing to null is allowed.
    cat = _category_id(client, "Groceries")
    assert client.post(
        f"/api/vendors/{vid}/set-default-category", json={"category_id": cat}
    ).status_code == 200
    assert client.post(
        f"/api/vendors/{vid}/set-default-category", json={"category_id": None}
    ).status_code == 200


def test_merge_vendor_repoints_set_vendor_and_vendor_equals_rules(db):
    """#13: merging re-points rules that reference the source vendor by id — a
    ``set_vendor`` action and a ``vendor_equals`` condition — so they don't dangle
    when the source is deleted (mirrors category_service.merge_category)."""
    from app.models import Rule

    source = vendor_service.create_vendor(db, {"canonical_name": "Src"})
    target = vendor_service.create_vendor(db, {"canonical_name": "Tgt"})
    sets = Rule(name="set", condition_type="merchant_contains", condition_value="x",
                action_type="set_vendor", action_value=str(source.id))
    matches = Rule(name="match", condition_type="vendor_equals", condition_value=str(source.id),
                   action_type="require_review", action_value=None)
    db.add_all([sets, matches])
    db.commit()

    vendor_service.merge_vendor(db, source.id, target.id)
    db.refresh(sets)
    db.refresh(matches)
    assert sets.action_value == str(target.id)        # set_vendor action re-pointed
    assert matches.condition_value == str(target.id)  # vendor_equals condition re-pointed


def test_merge_vendor_folds_default_category_and_last_seen(db):
    """The target adopts the source's default category when it lacks one, and keeps
    the more recent ``last_seen_at``."""
    from datetime import UTC, datetime

    cat = category_service.create_category(db, {"name": "Groceries"})
    source = vendor_service.create_vendor(db, {"canonical_name": "S", "default_category_id": cat.id})
    target = vendor_service.create_vendor(db, {"canonical_name": "T"})
    source.last_seen_at = datetime(2026, 6, 1, tzinfo=UTC)
    target.last_seen_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = vendor_service.merge_vendor(db, source.id, target.id)
    assert result.default_category_id == cat.id            # adopted from source
    assert result.last_seen_at.year == 2026 and result.last_seen_at.month == 6  # kept the later


def test_merge_vendor_self_merge_and_unknown(client):
    """Merging a vendor into itself is a 400; an unknown source or target is a 404."""
    a = client.post("/api/vendors", json={"canonical_name": "A"}).json()["id"]
    assert client.post(f"/api/vendors/{a}/merge", json={"target_id": a}).status_code == 400
    assert client.post(f"/api/vendors/{a}/merge", json={"target_id": 999999}).status_code == 404
    assert client.post("/api/vendors/999999/merge", json={"target_id": a}).status_code == 404


def test_merge_vendor_endpoint_repoints_and_owner_gated(client):
    """The endpoint re-points a transaction and deletes the source; a non-owner
    member is blocked (structural/destructive → owner only)."""
    client.post("/api/backup/demo")
    txn = client.get("/api/transactions", params={"limit": 1}).json()["items"][0]
    source = client.post("/api/vendors", json={"canonical_name": "MergeSrc"}).json()["id"]
    target = client.post("/api/vendors", json={"canonical_name": "MergeTgt"}).json()["id"]
    client.patch(f"/api/transactions/{txn['id']}", json={"merchant_id": source})

    # A non-owner member is blocked.
    hdr = {"X-Remote-User-Id": "ha-eve", "X-Remote-User-Display-Name": "Eve"}
    client.get("/api/users/me", headers=hdr)
    eve_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "ha-eve")
    client.patch(f"/api/users/{eve_id}", json={"role": "member", "status": "approved"})
    assert client.post(f"/api/vendors/{source}/merge", json={"target_id": target}, headers=hdr).status_code == 403

    # The owner succeeds; the transaction is re-pointed and the source is gone.
    res = client.post(f"/api/vendors/{source}/merge", json={"target_id": target})
    assert res.status_code == 200 and res.json()["id"] == target
    assert client.get(f"/api/vendors/{source}").status_code == 404
    moved = next(t for t in client.get("/api/transactions", params={"limit": 500}).json()["items"] if t["id"] == txn["id"])
    assert moved["merchant_id"] == target


def test_categorise_text_prefers_longest_keyword(db, monkeypatch):
    """The longest (most specific) matching keyword wins, not the first in file order —
    a generic 'cafe' must not shadow a specific 'cafe nero' in another category."""
    generic = category_service.create_category(db, {"name": "Drinks"})
    specific = category_service.create_category(db, {"name": "Coffee Shops"})
    generic.library_id = "test_generic"
    specific.library_id = "test_specific"
    db.commit()

    # The generic keyword is listed FIRST — old (first-match) code returned it.
    monkeypatch.setattr(category_service, "load_library", lambda: {"categories": [
        {"id": "test_generic", "keywords": ["cafe"]},
        {"id": "test_specific", "keywords": ["cafe nero"]},
    ]})
    cid, conf = category_service.categorise_text(db, "CAFE NERO LONDON")
    assert cid == specific.id  # longest keyword wins
    assert conf == category_service.KEYWORD_CONFIDENCE
    # A description matching only the generic keyword still resolves to it.
    assert category_service.categorise_text(db, "THE LOCAL CAFE")[0] == generic.id


def test_keyword_pattern_compiled_once(db):
    """The word-boundary regex for a keyword is compiled once and cached (SR-A2)."""
    first = category_service._keyword_pattern("cafe nero")
    second = category_service._keyword_pattern("cafe nero")
    assert first is second  # same compiled object reused, not recompiled


def test_categorise_map_cached_and_results_unchanged(db):
    """The library_id -> id map is built once and reused across calls, and repeated
    categorisation is deterministic (perf change must not alter results, SR-A2)."""
    category_service.import_library(db)

    first = category_service.categorise_text(db, "TESCO STORES 3142 DARTFORD")
    cached_map = category_service._lib_map_cache
    assert cached_map is not None  # cache warmed by the first call

    # A second call reuses the very same map object (no rebuild) and is identical.
    second = category_service.categorise_text(db, "TESCO STORES 3142 DARTFORD")
    assert second == first
    assert category_service._lib_map_cache is cached_map

    groceries = next(c for c in category_service.list_categories(db) if c.name == "Groceries")
    assert first[0] == groceries.id
    # Identity, not float ==: categorise_text returns the KEYWORD_CONFIDENCE constant.
    assert first[1] is category_service.KEYWORD_CONFIDENCE


def test_categorise_map_cache_reflects_runtime_category_change(db):
    """A runtime category change (here a delete) must invalidate the cached map so the
    next categorisation reflects it, rather than returning a stale id (SR-A2)."""
    category_service.import_library(db)
    subscriptions = next(
        c for c in category_service.list_categories(db) if c.name == "Subscriptions"
    )

    # Warm the cache: "netflix" keyword -> Subscriptions.
    first_id, _ = category_service.categorise_text(db, "NETFLIX.COM")
    assert first_id == subscriptions.id

    # Delete the category at runtime; the cache's version signal must invalidate.
    assert category_service.delete_category(db, subscriptions.id) is True
    second_id, second_conf = category_service.categorise_text(db, "NETFLIX.COM")
    assert second_id is None  # category gone -> no longer suggestable
    assert second_conf is None


def test_learn_vendor_category_reuses_existing_vendor(db):
    """A manual category correction reuses a vendor with the same canonical name instead
    of creating a duplicate when no alias happened to match the description."""
    from sqlalchemy import func, select

    from app.models import Vendor

    cat = category_service.create_category(db, {"name": "Groceries"})
    existing = vendor_service.create_vendor(db, {"canonical_name": "Tesco"})

    # A description matching no alias, but whose merchant_raw resolves to the same canonical.
    vendor = vendor_service.learn_vendor_category(db, "QWERTY SHOP 99", "Tesco", cat.id)

    assert vendor.id == existing.id  # reused, not duplicated
    assert vendor.default_category_id == cat.id
    count = db.scalar(
        select(func.count()).select_from(Vendor).where(func.lower(Vendor.canonical_name) == "tesco")
    )
    assert count == 1


def test_category_merge_validation(client):
    """Merging into itself is a 400; an unknown source or target is a 404."""
    a = client.post("/api/categories", json={"name": "X"}).json()["id"]
    assert client.post(f"/api/categories/{a}/merge", json={"target_id": a}).status_code == 400
    assert client.post(f"/api/categories/{a}/merge", json={"target_id": 999999}).status_code == 404
    assert client.post("/api/categories/999999/merge", json={"target_id": a}).status_code == 404


def test_global_privacy_applies_to_all_and_new(client):
    """One privacy level can be applied to every category at once; it becomes the
    default new categories inherit, and a bad level is rejected."""
    assert client.get("/api/categories/privacy").json()["level"] == "normal"  # default

    res = client.post("/api/categories/privacy", json={"level": "never_cloud"})
    assert res.status_code == 200 and res.json()["updated"] >= 22
    assert client.get("/api/categories/privacy").json()["level"] == "never_cloud"
    levels = {c["privacy_sensitivity"] for c in client.get("/api/categories").json()}
    assert levels == {"never_cloud"}  # every existing category updated

    # A category created afterwards inherits the new default.
    created = client.post("/api/categories", json={"name": "Inheritor"}).json()
    assert created["privacy_sensitivity"] == "never_cloud"

    assert client.post("/api/categories/privacy", json={"level": "bogus"}).status_code == 400


def test_admin_only_category_controls_reject_plain_members(client):
    """Cloud-AI privacy (settings-manager) and merge/delete (owner) are gated; an
    approved non-admin member without the settings grant gets 403 on all four,
    while the owner still succeeds (backlog #28 defence-in-depth)."""
    client.get("/api/users/me")  # owner bootstraps
    hdr = {"X-Remote-User-Id": "ha-eve", "X-Remote-User-Display-Name": "Eve"}
    client.get("/api/users/me", headers=hdr)  # Eve -> pending member
    eve_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "ha-eve")
    client.patch(f"/api/users/{eve_id}", json={"role": "member", "status": "approved"})

    cid = client.post("/api/categories", json={"name": "GateTest"}).json()["id"]
    other = client.post("/api/categories", json={"name": "GateTarget"}).json()["id"]

    # Member is blocked from the privacy + structural endpoints…
    assert client.get("/api/categories/privacy", headers=hdr).status_code == 403
    assert client.post("/api/categories/privacy", json={"level": "normal"}, headers=hdr).status_code == 403
    assert client.post(f"/api/categories/{cid}/merge", json={"target_id": other}, headers=hdr).status_code == 403
    assert client.delete(f"/api/categories/{cid}", headers=hdr).status_code == 403

    # …but the owner can still do all of them.
    assert client.get("/api/categories/privacy").status_code == 200
    assert client.post(f"/api/categories/{cid}/merge", json={"target_id": other}).status_code == 200
    assert client.delete(f"/api/categories/{other}").status_code == 204


# --- keyword auto-categorisation on import ---

def test_auto_categorisation_by_keyword(client, samples_dir):
    _import_curve(client, samples_dir)
    txns = client.get("/api/transactions").json()["items"]
    by_desc = {t["description_raw"]: t for t in txns}

    groceries = _category_id(client, "Groceries")
    diy = _category_id(client, "DIY")
    subscriptions = _category_id(client, "Subscriptions")
    transport = _category_id(client, "Transport")
    assert by_desc["TESCO STORES 3142 DARTFORD"]["category_id"] == groceries
    assert by_desc["SCREWFIX DIRECT DARTFORD"]["category_id"] == diy
    # Word-boundary matching: "netflix" -> Subscriptions, not Transport via the
    # "tfl" keyword being a substring of "neTFLix".
    assert by_desc["NETFLIX.COM"]["category_id"] == subscriptions
    assert by_desc["TfL TRAVEL CHARGE"]["category_id"] == transport
    # AMZNMKTPLACE has no keyword match -> stays uncategorised.
    assert by_desc["AMZNMKTPLACE*A1B2C3"]["category_id"] is None


def test_keyword_matching_is_word_boundary(db):
    category_service.import_library(db)
    # "tfl" must not match inside "netflix"; "netflix" -> Subscriptions.
    cat_id, _ = category_service.categorise_text(db, "NETFLIX.COM")
    sub = next(c for c in category_service.list_categories(db) if c.name == "Subscriptions")
    assert cat_id == sub.id
    # Prefix still works: "sainsbury" keyword matches "SAINSBURYS".
    cat_id2, _ = category_service.categorise_text(db, "SAINSBURYS S/MKT 0421")
    groceries = next(c for c in category_service.list_categories(db) if c.name == "Groceries")
    assert cat_id2 == groceries.id


# --- vendor alias matching ---

def test_vendor_alias_matching(db):
    category_service.import_library(db)
    groceries = next(c for c in category_service.list_categories(db) if c.name == "Groceries")
    vendor_service.create_vendor(
        db,
        {
            "canonical_name": "Tesco",
            "alias": "TESCO",
            "match_type": "contains",
            "default_category_id": groceries.id,
        },
    )
    vendor, match_type = vendor_service.match_vendor(db, "TESCO STORES 3142 DARTFORD")
    assert vendor is not None
    assert vendor.canonical_name == "Tesco"
    assert match_type == "contains"


def test_match_vendor_prefilter_preserves_all_match_types(db):
    """SR-A3 §1: the SQL-prefilter refactor of ``match_vendor`` returns the same
    results as the old full-scan for every match type (exact / contains / regex /
    fuzzy) and precedence still holds (exact > regex > contains > fuzzy)."""
    from app.models import Vendor, VendorAlias

    def _vendor(name: str, alias: str, match_type: str) -> Vendor:
        v = Vendor(canonical_name=name, display_name=name)
        db.add(v)
        db.flush()
        db.add(VendorAlias(vendor_id=v.id, alias=alias, match_type=match_type, source="user"))
        db.commit()
        return v

    exact = _vendor("Exactly", "PAYPAL *SPOTIFY", "exact")
    contains = _vendor("Containsco", "TESCO", "contains")
    rx = _vendor("Regexco", r"AMZN\w+", "regex")
    fz = _vendor("Fuzzyco", "STARBUCKS", "fuzzy")

    # exact
    v, mt = vendor_service.match_vendor(db, "PAYPAL *SPOTIFY")
    assert v is not None and v.id == exact.id and mt == "exact"
    # contains
    v, mt = vendor_service.match_vendor(db, "TESCO STORES 3142 DARTFORD")
    assert v is not None and v.id == contains.id and mt == "contains"
    # regex
    v, mt = vendor_service.match_vendor(db, "AMZNMKTPLACE ORDER")
    assert v is not None and v.id == rx.id and mt == "regex"
    # fuzzy (near-identical string, above threshold)
    v, mt = vendor_service.match_vendor(db, "STARBUCK")
    assert v is not None and v.id == fz.id and mt == "fuzzy"
    # no match
    assert vendor_service.match_vendor(db, "SOME UNKNOWN MERCHANT 999") == (None, None)
    # empty short-circuits
    assert vendor_service.match_vendor(db, "") == (None, None)


def test_match_vendor_precedence_exact_over_contains(db):
    """When two aliases match the same description, the higher-precedence type
    (exact) wins over the lower one (contains) — unchanged behaviour."""
    from app.models import Vendor, VendorAlias

    generic = Vendor(canonical_name="Generic")
    specific = Vendor(canonical_name="Specific")
    db.add_all([generic, specific])
    db.flush()
    db.add(VendorAlias(vendor_id=generic.id, alias="COFFEE SHOP LONDON", match_type="contains"))
    db.add(VendorAlias(vendor_id=specific.id, alias="COFFEE SHOP LONDON", match_type="exact"))
    db.commit()

    v, mt = vendor_service.match_vendor(db, "COFFEE SHOP LONDON")
    assert v.id == specific.id and mt == "exact"


def test_match_vendor_tie_break_prefers_longer_alias(db):
    """SR-A3 §3: on a same-match-type tie, the longer (more specific) alias wins
    instead of DB insertion order. The generic alias is inserted FIRST so the old
    first-wins code would have returned it."""
    from app.models import Vendor, VendorAlias

    generic = Vendor(canonical_name="Generic Coffee")
    specific = Vendor(canonical_name="Cafe Nero")
    db.add_all([generic, specific])
    db.flush()
    # Both 'contains' (same precedence); generic inserted first.
    db.add(VendorAlias(vendor_id=generic.id, alias="CAFE", match_type="contains"))
    db.add(VendorAlias(vendor_id=specific.id, alias="CAFE NERO", match_type="contains"))
    db.commit()

    v, _ = vendor_service.match_vendor(db, "CAFE NERO LONDON EC1")
    assert v.id == specific.id  # longer alias wins the tie

    # A description matching only the shorter alias still resolves to it.
    v2, _ = vendor_service.match_vendor(db, "THE LOCAL CAFE")
    assert v2.id == generic.id


def test_vendor_default_category_beats_keyword(client, samples_dir):
    # A vendor default category should win over the keyword fallback.
    shopping = _category_id(client, "Shopping")
    client.post(
        "/api/vendors",
        json={"canonical_name": "Amazon", "alias": "AMZNMKTPLACE", "default_category_id": shopping},
    )
    _import_curve(client, samples_dir)
    txns = client.get("/api/transactions").json()["items"]
    amazon = next(t for t in txns if t["description_raw"].startswith("AMZNMKTPLACE"))
    assert amazon["category_id"] == shopping
    assert amazon["merchant_id"] is not None


# --- manual categorisation + vendor learning (spec §15.3) ---

def test_manual_categorise_with_vendor_learning(client, samples_dir):
    _import_curve(client, samples_dir)
    txns = client.get("/api/transactions").json()["items"]
    amazon = next(t for t in txns if t["description_raw"].startswith("AMZNMKTPLACE"))
    shopping = _category_id(client, "Shopping")

    res = client.post(
        f"/api/transactions/{amazon['id']}/categorise",
        json={"category_id": shopping, "learn_vendor": True},
    )
    assert res.status_code == 200
    assert res.json()["category_id"] == shopping

    # A vendor was learned with that default category.
    vendors = client.get("/api/vendors").json()
    assert any(v["default_category_id"] == shopping for v in vendors)


def test_batch_categorise(client, samples_dir):
    _import_curve(client, samples_dir)
    txns = client.get("/api/transactions").json()["items"]
    ids = [t["id"] for t in txns[:3]]
    cash = _category_id(client, "Cash")
    res = client.post(
        "/api/transactions/categorise-batch", json={"transaction_ids": ids, "category_id": cash}
    )
    assert res.json()["updated"] == 3
    updated = client.get("/api/transactions").json()["items"]
    assert sum(1 for t in updated if t["category_id"] == cash) == 3


def test_recategorise_endpoint_scope_and_dry_run(client):
    """The recategorise endpoint honours the list filters (so you can target a
    subset) and a dry_run preview that reports a count without persisting."""
    client.post("/api/backup/demo")
    all_txns = client.get("/api/transactions", params={"limit": 500}).json()
    total = all_txns["total"]
    some_cat = next(t["category_id"] for t in all_txns["items"] if t["category_id"])
    in_cat = client.get("/api/transactions", params={"limit": 500, "category_id": some_cat}).json()["total"]
    blanks = client.get("/api/transactions", params={"limit": 500, "uncategorised": "true"}).json()["total"]

    # Unfiltered dry-run: considers every (non-archived) transaction, persists nothing.
    r = client.post("/api/transactions/recategorise", params={"dry_run": "true", "only_uncategorised": "false"})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert r.json()["considered"] == total

    # Filtered to one category → only those rows are in scope ("act on what you see").
    r2 = client.post(
        "/api/transactions/recategorise",
        params={"dry_run": "true", "only_uncategorised": "false", "category_id": some_cat},
    )
    assert r2.json()["considered"] == in_cat

    # only_uncategorised=true scopes to the blanks only.
    r3 = client.post("/api/transactions/recategorise", params={"dry_run": "true", "only_uncategorised": "true"})
    assert r3.json()["considered"] == blanks

    # The dry-runs above changed nothing.
    assert client.get("/api/transactions", params={"limit": 500}).json()["total"] == total


# --- dashboard ---

def test_dashboard_summary(client, samples_dir):
    _import_curve(client, samples_dir)
    summary = client.get("/api/dashboard/summary", params={"month": MONTH}).json()
    # Debits: 42.18+38.99+6.40+23.49+3.85+10.99+29.00 = 154.90; salary 2450 income.
    assert summary["spend_this_month"] == "154.90"
    assert summary["income_this_month"] == "2450.00"
    assert summary["net_this_month"] == "2295.10"
    assert summary["total_transactions"] == 8


def test_dashboard_category_breakdown(client, samples_dir):
    _import_curve(client, samples_dir)
    rows = client.get("/api/dashboard/categories", params={"month": MONTH}).json()
    totals = {r["name"]: r["total"] for r in rows}
    assert totals["Groceries"] == "42.18"
    assert totals["DIY"] == "38.99"
    assert totals["Subscriptions"] == "10.99"  # Netflix
    assert totals["Transport"] == "6.40"  # TfL only (not Netflix)
    assert totals["Uncategorised"] == "23.49"  # the Amazon row
    # Income (a credit) must not appear in the spend breakdown.
    assert "Income" not in totals


def test_category_cloud_privacy_is_user_editable(client):
    """A user can choose what each category sends to cloud AI (#28): a category's
    privacy level is editable (e.g. lock 'Income' to never_cloud), and invalid
    levels are rejected on both update and create."""
    income_id = _category_id(client, "Income")

    patched = client.patch(
        f"/api/categories/{income_id}", json={"privacy_sensitivity": "never_cloud"}
    )
    assert patched.status_code == 200
    assert patched.json()["privacy_sensitivity"] == "never_cloud"

    # It persists on the list view (so the 🔒 shows in the UI).
    income = next(c for c in client.get("/api/categories").json() if c["id"] == income_id)
    assert income["privacy_sensitivity"] == "never_cloud"

    # Invalid levels are rejected (defense-in-depth) on update and create.
    assert client.patch(
        f"/api/categories/{income_id}", json={"privacy_sensitivity": "public"}
    ).status_code == 400
    assert client.post(
        "/api/categories", json={"name": "Bogus", "privacy_sensitivity": "weird"}
    ).status_code == 400


def test_set_vendor_on_transaction(client):
    """A user can manually assign (and clear) a vendor on a transaction row
    (#15.3); a bogus vendor id is rejected."""
    client.post("/api/backup/demo")
    txn_id = client.get("/api/transactions", params={"limit": 1}).json()["items"][0]["id"]
    vendor_id = client.get("/api/vendors").json()[0]["id"]

    assigned = client.patch(f"/api/transactions/{txn_id}", json={"merchant_id": vendor_id})
    assert assigned.status_code == 200
    assert assigned.json()["merchant_id"] == vendor_id

    # A non-existent vendor is rejected.
    assert client.patch(f"/api/transactions/{txn_id}", json={"merchant_id": 999_999}).status_code == 400

    # Clearing back to no vendor works.
    cleared = client.patch(f"/api/transactions/{txn_id}", json={"merchant_id": None})
    assert cleared.status_code == 200
    assert cleared.json()["merchant_id"] is None


def test_bulk_update_transactions(client):
    """Multi-edit applies one change to many transactions at once: set category,
    add a tag, archive, delete — scope-safe and validated."""
    client.post("/api/backup/demo")
    ids = [t["id"] for t in client.get("/api/transactions", params={"limit": 3}).json()["items"]]
    cat = _category_id(client, "Groceries")

    # Bulk set category.
    r = client.post("/api/transactions/bulk", json={"transaction_ids": ids, "category_id": cat})
    assert r.status_code == 200 and r.json()["updated"] == len(ids)
    after = {t["id"]: t for t in client.get("/api/transactions", params={"limit": 500}).json()["items"]}
    assert all(after[i]["category_id"] == cat for i in ids)

    # Bulk add a tag (appends, doesn't replace).
    client.post("/api/transactions/bulk", json={"transaction_ids": ids, "add_tag": "bulk-test"})
    tagged = {t["id"]: t for t in client.get("/api/transactions", params={"limit": 500}).json()["items"]}
    assert all(any(tg["name"] == "bulk-test" for tg in tagged[i]["tags"]) for i in ids)

    # An unknown category id fails the whole call.
    assert client.post(
        "/api/transactions/bulk", json={"transaction_ids": ids, "category_id": 999_999}
    ).status_code == 400

    # Bulk set a country (the Travel "set country for this trip" action) — normalised upper-case.
    client.post("/api/transactions/bulk", json={"transaction_ids": ids, "country": "es"})
    withc = {t["id"]: t for t in client.get("/api/transactions", params={"limit": 500}).json()["items"]}
    assert all(withc[i]["country"] == "ES" for i in ids)

    # Bulk archive → hidden from the default list, shown with include_archived.
    client.post("/api/transactions/bulk", json={"transaction_ids": ids, "archive": True})
    visible = {t["id"] for t in client.get("/api/transactions", params={"limit": 500}).json()["items"]}
    assert not (set(ids) & visible)
    incl = {
        t["id"]
        for t in client.get("/api/transactions", params={"limit": 500, "include_archived": "true"}).json()["items"]
    }
    assert set(ids) <= incl

    # Bulk delete → gone entirely.
    r = client.post("/api/transactions/bulk", json={"transaction_ids": ids, "delete": True})
    assert r.json()["deleted"] == len(ids)
    gone = {
        t["id"]
        for t in client.get("/api/transactions", params={"limit": 500, "include_archived": "true"}).json()["items"]
    }
    assert not (set(ids) & gone)


def test_delete_by_filter_scoped_then_all(client):
    """delete-by-filter removes just the filtered subset, taking a safety backup
    first; with no filter it deletes everything. A no-op takes no backup."""
    client.get("/api/users/me")  # bootstrap the local owner
    client.post("/api/backup/demo")

    total0 = client.get("/api/transactions", params={"limit": 500}).json()["total"]
    assert total0 > 0
    cat = next(t["category_id"] for t in client.get("/api/transactions", params={"limit": 500}).json()["items"] if t["category_id"])
    in_cat = client.get("/api/transactions", params={"limit": 500, "category_id": cat}).json()["total"]
    assert 0 < in_cat <= total0

    # Delete only the rows in that one category (a safety backup is taken first).
    r = client.post("/api/transactions/delete-by-filter", params={"category_id": cat})
    assert r.status_code == 200
    assert r.json() == {"deleted": in_cat, "backup_taken": True}
    after = client.get("/api/transactions", params={"limit": 500}).json()
    assert after["total"] == total0 - in_cat
    assert all(t["category_id"] != cat for t in after["items"])

    # Delete everything that's left (no filter = all).
    r2 = client.post("/api/transactions/delete-by-filter")
    assert r2.json()["deleted"] == total0 - in_cat
    assert client.get("/api/transactions", params={"limit": 500}).json()["total"] == 0

    # Nothing left → a no-op that takes no backup.
    assert client.post("/api/transactions/delete-by-filter").json() == {"deleted": 0, "backup_taken": False}


def test_delete_by_filter_is_owner_only(client):
    """The mass delete is owner-gated: a member is refused (403) and no rows go."""
    mem = {"X-Remote-User-Id": "mem", "X-Remote-User-Display-Name": "mem"}
    client.get("/api/users/me")  # owner
    client.post("/api/backup/demo")
    client.get("/api/users/me", headers=mem)  # second user → pending
    uid = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "mem")
    client.patch(f"/api/users/{uid}", json={"role": "member", "status": "approved"})

    before = client.get("/api/transactions", params={"limit": 500}).json()["total"]
    assert client.post("/api/transactions/delete-by-filter", headers=mem).status_code == 403
    assert client.get("/api/transactions", params={"limit": 500}).json()["total"] == before


def test_list_filters_drill_down(client):
    """The Dashboard/Vendors/Business links narrow the Transactions list by
    country, category and vendor — verify each filter returns matching rows."""
    client.post("/api/backup/demo")
    items = client.get("/api/transactions", params={"limit": 500}).json()["items"]
    assert len(items) > 3
    ids = [t["id"] for t in items[:3]]

    # Country (spend-by-location drill-down): tag a few rows ES, ?country=ES returns
    # exactly those; the match is case-insensitive and an untagged country is empty.
    client.post("/api/transactions/bulk", json={"transaction_ids": ids, "country": "es"})
    by_country = client.get("/api/transactions", params={"country": "ES", "limit": 500}).json()
    assert {t["id"] for t in by_country["items"]} == set(ids)
    assert client.get("/api/transactions", params={"country": "fr", "limit": 500}).json()["total"] == 0

    # Category (spend-by-category drill-down): every returned row has the category.
    cat = _category_id(client, "Groceries")
    client.post("/api/transactions/bulk", json={"transaction_ids": ids, "category_id": cat})
    by_cat = client.get("/api/transactions", params={"category_id": cat, "limit": 500}).json()
    assert set(ids) <= {t["id"] for t in by_cat["items"]}
    assert all(t["category_id"] == cat for t in by_cat["items"])

    # Vendor (top-vendors drill-down): assign a fresh vendor, ?vendor_id returns just those.
    vid = client.post("/api/vendors", json={"canonical_name": "DrillVendor"}).json()["id"]
    client.post("/api/transactions/bulk", json={"transaction_ids": ids, "merchant_id": vid})
    by_vendor = client.get("/api/transactions", params={"vendor_id": vid, "limit": 500}).json()
    assert {t["id"] for t in by_vendor["items"]} == set(ids)


def test_set_transaction_country_via_patch(client, samples_dir):
    """A single transaction's country can be set (and cleared) via PATCH — beats
    the vendor's country for the map. Normalised to upper-case ISO-2; "" clears."""
    _import_curve(client, samples_dir)
    tid = client.get("/api/transactions").json()["items"][0]["id"]
    assert client.patch(f"/api/transactions/{tid}", json={"country": "es"}).json()["country"] == "ES"
    assert client.patch(f"/api/transactions/{tid}", json={"country": ""}).json()["country"] is None


def test_country_filter_matches_resolved_country(client, samples_dir):
    """The spend-by-location drill-down matches a transaction's RESOLVED country
    (here, its vendor's) — not only a stored txn.country. Otherwise clicking a
    country on the map lands on an empty Transactions list."""
    _import_curve(client, samples_dir)
    ids = [t["id"] for t in client.get("/api/transactions").json()["items"][:2]]
    vid = client.post("/api/vendors", json={"canonical_name": "Carrefour"}).json()["id"]
    client.patch(f"/api/vendors/{vid}", json={"country": "FR"})
    client.post("/api/transactions/bulk", json={"transaction_ids": ids, "merchant_id": vid})

    fr = client.get("/api/transactions", params={"country": "FR", "limit": 500}).json()
    found = {t["id"] for t in fr["items"]}
    assert set(ids) <= found                                # found via the vendor's country…
    assert all(t["country"] is None for t in fr["items"])   # …despite no stored txn.country
