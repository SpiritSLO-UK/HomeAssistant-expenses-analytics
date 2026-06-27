"""RFC 6238 TOTP (time-based one-time passwords), pure standard library.

Used for optional app-level MFA (backlog #124). Implemented in-house rather than
adding a dependency: it's ~40 lines of stdlib (hmac/hashlib/base64), works on
every platform, and matches Google Authenticator / Authy / Aegis defaults
(SHA-1, 6 digits, 30-second period).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote, urlencode

PERIOD = 30
DIGITS = 6
ALGORITHM = "SHA1"
# Hard cap on the clock-skew window any caller can request (±2 periods = ±60s),
# so a code can't be made to verify over an unboundedly wide interval (SR-E3).
_MAX_WINDOW = 2


def generate_secret(num_bytes: int = 20) -> str:
    """A new random base32 secret (no padding), as authenticator apps expect."""
    return base64.b32encode(os.urandom(num_bytes)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = DIGITS) -> str:
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def matched_counter(
    secret_b32: str,
    code: str,
    *,
    period: int = PERIOD,
    digits: int = DIGITS,
    window: int = 1,
    now: float | None = None,
) -> int | None:
    """The timestep counter that ``code`` matches within ±``window`` periods, or
    ``None`` if it doesn't match. The counter lets callers enforce one-time use
    (reject a code at a counter already consumed — CR-SEC-5)."""
    if not secret_b32 or not code:
        return None
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return None
    # Clamp the skew window so a caller can never widen the acceptance interval
    # far enough to weaken the one-time code (SR-E3). ±2 periods = ±60s.
    window = max(0, min(window, _MAX_WINDOW))
    counter = int((now if now is not None else time.time()) // period)
    for drift in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret_b32, counter + drift, digits), code):
            return counter + drift
    return None


def verify(
    secret_b32: str,
    code: str,
    *,
    period: int = PERIOD,
    digits: int = DIGITS,
    window: int = 1,
    now: float | None = None,
) -> bool:
    """True if ``code`` matches the secret within ±``window`` periods (clock skew)."""
    return matched_counter(secret_b32, code, period=period, digits=digits, window=window, now=now) is not None


def current_code(
    secret_b32: str, *, period: int = PERIOD, digits: int = DIGITS, now: float | None = None
) -> str:
    """The code valid right now — used by tests (and never sent to a client)."""
    counter = int((now if now is not None else time.time()) // period)
    return _hotp(secret_b32, counter, digits)


def provisioning_uri(secret_b32: str, account_name: str, issuer: str) -> str:
    """`otpauth://` URI an authenticator app can scan (as a QR) or accept by hand."""
    label = quote(f"{issuer}:{account_name}")
    params = urlencode(
        {
            "secret": secret_b32,
            "issuer": issuer,
            "digits": DIGITS,
            "period": PERIOD,
            "algorithm": ALGORITHM,
        }
    )
    return f"otpauth://totp/{label}?{params}"
