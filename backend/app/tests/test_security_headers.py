"""Content-Security-Policy header (CR-FEAT-8).

Verifies the backend stamps a CSP on responses, that it carries the key directives,
and — crucially — that the inline-theme-script hash baked into ``script-src`` still
matches the actual inline script in ``frontend/index.html`` (so an edit to that script
can't silently break the app under the enforced policy without failing CI here).
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

# Repo root: backend/app/tests/ -> parents[3].
_INDEX_HTML = Path(__file__).resolve().parents[3] / "frontend" / "index.html"


def _inline_script_sha256() -> str:
    """Recompute the CSP hash from the real inline theme script the way a browser
    does: over the LF-normalised UTF-8 text content of the first inline <script>."""
    raw = _INDEX_HTML.read_bytes()
    # The HTML parser normalises CRLF and lone CR to LF before hashing.
    text = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").decode("utf-8")
    match = re.search(r"<script>(.*?)</script>", text, re.S)
    assert match is not None, "inline theme script not found in frontend/index.html"
    digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode()


def test_csp_header_present_with_key_directives(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200

    csp = resp.headers.get("Content-Security-Policy")
    assert csp, "Content-Security-Policy header is missing"

    # Key directives that must be present for the mitigation to be meaningful.
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "frame-ancestors 'self'" in csp
    # Ingress must not be broken by a 'none' frame policy.
    assert "frame-ancestors 'none'" not in csp
    # The token-theft mitigation depends on script-src NOT allowing arbitrary inline JS.
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]


def test_csp_allows_the_real_inline_theme_script(client):
    """The script-src hash must match the current inline theme script exactly, or the
    enforced CSP would block it and the SPA would fail to theme on load."""
    csp = client.get("/api/health").headers["Content-Security-Policy"]
    assert f"'{_inline_script_sha256()}'" in csp


def test_csp_stamped_on_error_responses(client):
    """The header is added by the outermost middleware, so short-circuit error
    responses (here a 404) carry it too."""
    resp = client.get("/api/definitely-not-a-real-endpoint")
    assert resp.status_code == 404
    assert resp.headers.get("Content-Security-Policy")
