"""The single source of "now" (design: Core modules, clock.py).

All application code reads the time through these functions, so tests can fix
the clock with freeze() instead of depending on when they happen to run.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/Phoenix")  # D6: Arizona time, UTC-7, no daylight saving

# Dev/demo override: set CLOCK_OVERRIDE to an ISO-8601 datetime to pretend "now"
# is that moment in every process that reads the clock (web server, price
# generator, CLI). Handy for forcing a weekday so the market opens on a weekend.
# Naive values are read in the market time zone. Unset the variable to go back
# to real time. This is a jury rig for local use, not for production.
CLOCK_OVERRIDE_ENV = "CLOCK_OVERRIDE"

_frozen: datetime | None = None


def _env_override() -> datetime | None:
    raw = os.environ.get(CLOCK_OVERRIDE_ENV)
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):  # tolerate a trailing Z for UTC
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:  # a bare date/time is read as market-local
        parsed = parsed.replace(tzinfo=MARKET_TZ)
    return parsed.astimezone(timezone.utc)


def now_utc() -> datetime:
    """Current time as an aware UTC datetime.

    Precedence: a test freeze() wins, then the CLOCK_OVERRIDE env var, then the
    real wall clock.
    """
    if _frozen is not None:
        return _frozen
    override = _env_override()
    if override is not None:
        return override
    return datetime.now(timezone.utc)


def now_local(tz: ZoneInfo = MARKET_TZ) -> datetime:
    """Current time in the market time zone (Arizona by default)."""
    return now_utc().astimezone(tz)


def freeze(at: datetime) -> None:
    """Fix the clock at `at` (tests only). `at` must be timezone-aware."""
    global _frozen
    if at.tzinfo is None:
        raise ValueError("freeze() needs a timezone-aware datetime")
    _frozen = at.astimezone(timezone.utc)


def unfreeze() -> None:
    global _frozen
    _frozen = None
