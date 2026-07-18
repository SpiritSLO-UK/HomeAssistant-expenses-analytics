"""Segment-boundary matching for the auth-guard exempt/allowed prefix sets.

The gate-exempt / disabled-allowed / lock-exempt / self-service / child-allowed sets
used to be matched with a bare ``str.startswith``, so any sibling path sharing a prefix
slipped through: ``"/api/users/members".startswith("/api/users/me")`` is ``True`` — the
prefix collision behind the CR-SEC-16 roster leak (route-gated in #368). ``_path_in``
now matches on path-segment boundaries: ``/api/users/me`` exempts only ``/api/users/me``
and ``/api/users/me/…``, never ``/api/users/members``. Genuine subtrees such as
``/api/security`` and ``/api/allowance/summary`` keep matching their descendants.
"""

from __future__ import annotations

from app.main import (
    _CHILD_ALLOWED_PREFIXES,
    _DISABLED_ALLOWED,
    _GATE_EXEMPT,
    _LOCK_EXEMPT,
    _SELF_SERVICE,
    _path_in,
)


def _hdr(uid: str, name: str) -> dict:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name}


# --- unit: the collision is closed, exact + intended subtrees still match ----------


def test_members_is_not_gate_exempt_but_me_is():
    # The exact CR-SEC-16 collision: a sibling of the exempt path must NOT be exempt.
    assert _path_in("/api/users/members", _GATE_EXEMPT) is False
    # ...while the genuinely-exempt path (and any descendant of it) still is.
    assert _path_in("/api/users/me", _GATE_EXEMPT) is True
    assert _path_in("/api/users/me/", _GATE_EXEMPT) is True
    assert _path_in("/api/users/me/settings", _GATE_EXEMPT) is True


def test_gate_exempt_keeps_health_and_security_subtree():
    assert _path_in("/api/health", _GATE_EXEMPT) is True
    # /api/security is an intentional subtree (unlock/status routes) — descendants match.
    assert _path_in("/api/security", _GATE_EXEMPT) is True
    assert _path_in("/api/security/status", _GATE_EXEMPT) is True
    # ...but a sibling that merely shares the text prefix does not.
    assert _path_in("/api/securityaudit", _GATE_EXEMPT) is False


def test_disabled_allowed_matches_me_only_not_members():
    assert _path_in("/api/users/me", _DISABLED_ALLOWED) is True
    assert _path_in("/api/users/members", _DISABLED_ALLOWED) is False
    assert _path_in("/api/health", _DISABLED_ALLOWED) is True


def test_lock_exempt_keeps_security_subtree_only():
    assert _path_in("/api/security/unlock", _LOCK_EXEMPT) is True
    assert _path_in("/api/health", _LOCK_EXEMPT) is True
    assert _path_in("/api/securityfoo", _LOCK_EXEMPT) is False


def test_self_service_matches_mfa_subtree_not_siblings():
    assert _path_in("/api/auth/mfa", _SELF_SERVICE) is True
    assert _path_in("/api/auth/mfa/verify", _SELF_SERVICE) is True
    assert _path_in("/api/auth/mfalogin", _SELF_SERVICE) is False


def test_child_allowed_matches_summary_subtree_not_siblings():
    assert _path_in("/api/allowance/summary", _CHILD_ALLOWED_PREFIXES) is True
    assert _path_in("/api/allowance/summary/2026", _CHILD_ALLOWED_PREFIXES) is True
    assert _path_in("/api/allowance/summaries", _CHILD_ALLOWED_PREFIXES) is False


def test_empty_prefixes_never_match():
    assert _path_in("/api/anything", ()) is False


# --- integration: the middleware no longer exempts the roster sibling --------------


def test_pending_user_blocked_from_members_but_sees_own_status(client):
    client.get("/api/users/me")  # headerless → local owner (bootstrap)
    zoe = _hdr("ha-zoe", "Zoe")
    client.get("/api/users/me", headers=zoe)  # Zoe appears → pending

    # The roster sibling is NOT gate-exempt anymore: a pending account is blocked
    # (defence in depth on top of the #368 route guard).
    assert client.get("/api/users/members", headers=zoe).status_code == 403
    # ...but the genuinely-exempt /api/users/me still lets it learn it's pending.
    me = client.get("/api/users/me", headers=zoe)
    assert me.status_code == 200 and me.json()["status"] == "pending"
