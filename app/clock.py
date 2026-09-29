"""The single source of "now" (design: Core modules, clock.py).

All application code reads the time through these functions, so tests can fix
the clock with freeze() instead of depending on when they happen to run.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/Phoenix")  # D6: Arizona time, UTC-7, no daylight saving

_frozen: datetime | None = None


def now_utc() -> datetime:
    """Current time as an aware UTC datetime."""
    return _frozen if _frozen is not None else datetime.now(timezone.utc)


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
