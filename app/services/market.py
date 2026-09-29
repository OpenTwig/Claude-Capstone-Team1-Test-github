"""Market calendar: when the market is open (design: Market clock; R15, AR05, D4, D6).

is_open(), next_open(), and market_status() are pure: the schedule is passed in.
current_schedule(), save_schedule(), is_market_open(), and current_status() use
the schedule administrators save in the MarketSchedule table (task 2.4).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays

from app.clock import MARKET_TZ
from app.services.types import MarketStatus


@dataclass(frozen=True)
class Schedule:
    open_time: time = time(9, 0)
    close_time: time = time(17, 0)

    def __post_init__(self):
        if self.close_time <= self.open_time:
            raise ValueError("close time must be after open time")


DEFAULT_SCHEDULE = Schedule()  # AR05: 9:00 AM to 5:00 PM


@lru_cache(maxsize=None)
def _nyse_holidays(year: int) -> frozenset[date]:
    return frozenset(holidays.NYSE(years=year).keys())


def is_holiday(day: date) -> bool:
    """NYSE holidays (D4, A16)."""
    return day in _nyse_holidays(day.year)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and not is_holiday(day)


def _local(at_utc: datetime, tz: ZoneInfo) -> datetime:
    if at_utc.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime")
    return at_utc.astimezone(tz)


def is_open(at_utc: datetime, schedule: Schedule = DEFAULT_SCHEDULE, tz: ZoneInfo = MARKET_TZ) -> bool:
    """True if the market is open at this instant: a trading day, and open <= time < close."""
    local = _local(at_utc, tz)
    if not is_trading_day(local.date()):
        return False
    return schedule.open_time <= local.time().replace(tzinfo=None) < schedule.close_time


def next_open(at_utc: datetime, schedule: Schedule = DEFAULT_SCHEDULE, tz: ZoneInfo = MARKET_TZ) -> datetime:
    """The next moment the market opens, strictly after `at_utc`, in market-local time."""
    local = _local(at_utc, tz)
    day = local.date()
    for _ in range(366 * 2):  # there is always a trading day within a year
        candidate = datetime.combine(day, schedule.open_time, tzinfo=tz)
        if is_trading_day(day) and candidate > local:
            return candidate
        day += timedelta(days=1)
    raise RuntimeError("no trading day found within two years")


def market_status(at_utc: datetime, schedule: Schedule = DEFAULT_SCHEDULE, tz: ZoneInfo = MARKET_TZ) -> MarketStatus:
    """Open/closed, plus when it closes (if open) or next opens (if closed), for the Trade page (R15.6)."""
    local = _local(at_utc, tz)
    if is_open(at_utc, schedule, tz):
        return MarketStatus(True, datetime.combine(local.date(), schedule.close_time, tzinfo=tz), None)
    return MarketStatus(False, None, next_open(at_utc, schedule, tz))


# --- Stored schedule (task 2.4; R15.3-R15.5, AR04) ---------------------------------------------


def market_tz() -> ZoneInfo:
    """The configured market time zone (MARKET_TIMEZONE, Arizona by default; D6)."""
    from flask import current_app

    return ZoneInfo(current_app.config["SETTINGS"].market_timezone)


def _as_time(value) -> time:
    """PyMySQL returns TIME columns as timedelta; convert to a time of day."""
    if isinstance(value, time):
        return value
    seconds = int(value.total_seconds())
    return time(seconds // 3600, (seconds // 60) % 60, seconds % 60)


def current_schedule(cursor=None) -> Schedule:
    """The most recently saved market hours, or the default 9:00-17:00 (AR05).

    The hours apply every trading day until changed; there are no dates (D21). Pass the
    cursor of an open transaction to read inside it (nothing is committed then).
    """
    from app.db import get_db

    sql = "SELECT open_time, close_time FROM MarketSchedule ORDER BY updated_at DESC, scheduleID DESC LIMIT 1"
    if cursor is not None:
        cursor.execute(sql)
        found = cursor.fetchone()
    else:
        with get_db().cursor() as cur:
            cur.execute(sql)
            found = cur.fetchone()
        get_db().commit()
    if found is None:
        return DEFAULT_SCHEDULE
    return Schedule(_as_time(found["open_time"]), _as_time(found["close_time"]))


def save_schedule(admin_user_id: int, open_time: time | None, close_time: time | None) -> None:
    """Change the market hours (R15.3, R15.4, D21). An invalid schedule leaves the current one in place (R15.5)."""
    from app.db import transaction
    from app.errors import ValidationFailed
    from app.services.users import require_admin

    require_admin(admin_user_id)
    if open_time is None:
        raise ValidationFailed("open_time", "Open time is required.")
    if close_time is None:
        raise ValidationFailed("close_time", "Close time is required.")
    if close_time <= open_time:
        raise ValidationFailed("close_time", "Close time must be after open time.")
    with transaction() as cur:
        cur.execute(
            "INSERT INTO MarketSchedule (open_time, close_time, updated_by) VALUES (%s, %s, %s)",
            (open_time, close_time, admin_user_id),
        )


def is_market_open(at_utc: datetime | None = None) -> bool:
    """Is the market open now (or at `at_utc`), using the saved schedule? Used by trading (R8.4, R9.3)."""
    from app import clock

    return is_open(at_utc or clock.now_utc(), current_schedule(), market_tz())


def current_status(at_utc: datetime | None = None) -> MarketStatus:
    """Open/closed and the next change, using the saved schedule (R15.6)."""
    from app import clock

    return market_status(at_utc or clock.now_utc(), current_schedule(), market_tz())
