"""Abuse guards for the AI gateway (backlog: harden AI endpoints).

Threat model: someone who already has app access (a household member, or an
attacker who reached the UI) must not be able to pump arbitrary or oversized
content through the AI gateway, or run up unbounded cloud cost. Three
process-local guards, each with a settings knob (0 disables it):

- **Per-user rate limit** on the AI-dispatching POST routes: a sliding
  one-minute window per user id (``ai_rate_limit_per_minute``, default 30).
  Mirrors the MFA brute-force throttle (mfa_service, CR-SEC-6): this is a
  single-process app, so in-memory process-lifetime state is enough.
- **Payload size cap** on those routes (``ai_max_payload_bytes``, default
  100 KB). The JSON bodies these routes accept are tiny (id lists), so a
  large body is abuse, not use. Raw images go through the upload routes,
  which keep their own separate 15 MB cap (uploads.AI_IMAGE_MAX mirrored by
  ai_service._MAX_IMAGE_BYTES).
- **Daily budget cap**: at most ``ai_daily_request_cap`` AI requests per UTC
  day (default 500), counted from the ``AIRequest`` audit rows the gateway
  already writes for every call. Token counts are NOT stored on ``AIRequest``,
  so this is a request-count budget, not a token budget.

Defaults are ceilings, not throttles: normal single-user use (including the
local-LLM batch flow) never notices them. The functions here return facts;
the caller maps them to HTTP (429/413) - the same split as
``mfa_service.mfa_lockout_seconds`` / ``routes_auth``.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AIRequest

RATE_WINDOW = timedelta(minutes=1)
_recent_ai_calls: dict[int, list[datetime]] = {}


def _now() -> datetime:
    # Naive UTC, matching AIRequest.created_at (SQLite CURRENT_TIMESTAMP is UTC).
    return datetime.now(UTC).replace(tzinfo=None)


def _recent(user_id: int) -> list[datetime]:
    """Prune and return the user's calls still inside the sliding window."""
    cutoff = _now() - RATE_WINDOW
    times = [t for t in _recent_ai_calls.get(user_id, []) if t >= cutoff]
    if times:
        _recent_ai_calls[user_id] = times
    else:
        _recent_ai_calls.pop(user_id, None)
    return times


def rate_limit_wait_seconds(user_id: int) -> int:
    """0 = this call is allowed (and recorded); else seconds until a slot frees.

    Sliding window: the oldest recorded call ageing out of ``RATE_WINDOW`` is
    what frees a slot, so the wait is derived from it (never 0 while blocked).
    """
    limit = settings.ai_rate_limit_per_minute
    if limit <= 0:
        return 0  # knob disabled
    times = _recent(user_id)
    if len(times) >= limit:
        return max(1, int((min(times) + RATE_WINDOW - _now()).total_seconds()))
    _recent_ai_calls.setdefault(user_id, []).append(_now())
    return 0


def oversize_payload_cap(content_length: str | None) -> int | None:
    """The configured byte cap when the declared body size exceeds it, else None.

    A cheap early refusal based on the Content-Length header. A missing or
    malformed header passes here - the routes' pydantic body parsing still
    bounds what is accepted, and every normal client declares the length.
    """
    cap = settings.ai_max_payload_bytes
    if cap <= 0 or content_length is None:
        return None
    try:
        declared = int(content_length)
    except ValueError:
        return None
    return cap if declared > cap else None


def daily_cap_reached(db: Session) -> tuple[int, int] | None:
    """``(used, cap)`` when today's AI request count has hit the daily budget,
    else None. Counts every ``AIRequest`` audit row created today (UTC) - the
    gateway writes one per dispatched or staged call, so staging floods count
    too."""
    cap = settings.ai_daily_request_cap
    if cap <= 0:
        return None
    day_start = datetime.combine(_now().date(), time.min)
    used = db.scalar(select(func.count()).select_from(AIRequest).where(AIRequest.created_at >= day_start)) or 0
    return (used, cap) if used >= cap else None


def daily_budget_message(used: int, cap: int) -> str:
    """The one detail string for a tripped daily budget, shared by the route
    guard (429) and the service-level vision guard (AIDisabled)."""
    return (
        f"Daily AI budget reached ({used}/{cap} requests today). "
        "Try again tomorrow, or raise HAFI_AI_DAILY_REQUEST_CAP."
    )


def reset_throttle() -> None:
    """Clear ALL rate-limit state - used by tests for isolation (process-global)."""
    _recent_ai_calls.clear()
