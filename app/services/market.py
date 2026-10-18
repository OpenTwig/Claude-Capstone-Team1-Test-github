"""Market calendar: when the market is open (design: Market clock; R15, AR05, D4, D6, D29).

is_open(), next_open(), and market_status() are pure: the schedule is passed in.
current_schedule(), save_schedule(), is_market_open(), and current_status() use
the schedule administrators save in the MarketSchedule table (task 2.4, task 6.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays

from app.clock import MARKET_TZ
from app.services.types import MarketStatus

# Trading days are date.weekday() numbers: 0 = Monday ... 6 = Sunday (D29).
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
ALL_DAYS = frozenset(range(7))
MON_TO_FRI = frozenset(range(5))  # AR05


@dataclass(frozen=True)
class Schedule:
    open_time: time = time(9, 0)
    close_time: time = time(17, 0)
    trading_days: frozenset[int] = MON_TO_FRI
    force_open: bool = False  # D29: open at every moment until an administrator switches it off

    def __post_init__(self):
        if self.close_time <= self.open_time:
            raise ValueError("close time must be after open time")
        days = frozenset(self.trading_days)
        if not days or not days <= ALL_DAYS:
            raise ValueError("trading days must be one or more of 0 (Monday) to 6 (Sunday)")
        object.__setattr__(self, "trading_days", days)


DEFAULT_SCHEDULE = Schedule()  # AR05: 9:00 AM to 5:00 PM, Monday to Friday


def days_to_mask(days) -> int:
    """MarketSchedule.trading_days stores the days as a bit mask: bit n is weekday n (DV12)."""
    return sum(1 << d for d in set(days))


def days_from_mask(mask: int) -> frozenset[int]:
    return frozenset(d for d in range(7) if mask & (1 << d))


def _day_runs(days) -> list[tuple[int, int]]:
    """Runs of consecutive days, Monday first: {0, 1, 2, 4} -> [(0, 2), (4, 4)]."""
    runs: list[tuple[int, int]] = []
    for d in sorted(set(days)):
        if runs and runs[-1][1] == d - 1:
            runs[-1] = (runs[-1][0], d)
        else:
            runs.append((d, d))
    return runs


def days_text(days) -> str:
    """The days in words for the Market Time card: "Monday to Friday", "every day", "Monday, Wednesday and Friday"."""
    if set(days) == ALL_DAYS:
        return "every day"
    parts: list[str] = []
    for first, last in _day_runs(days):
        if last - first >= 2:
            parts.append(f"{WEEKDAY_NAMES[first]} to {WEEKDAY_NAMES[last]}")
        else:
            parts.extend(WEEKDAY_NAMES[first:last + 1])
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def days_short(days) -> str:
    """The Trading Days button label: "Mon–Fri", "Every day", "Mon, Wed, Fri" (admin.js builds the same)."""
    if set(days) == ALL_DAYS:
        return "Every day"
    parts: list[str] = []
    for first, last in _day_runs(days):
        if last - first >= 2:
            parts.append(f"{WEEKDAY_NAMES[first][:3]}–{WEEKDAY_NAMES[last][:3]}")
        else:
            parts.extend(name[:3] for name in WEEKDAY_NAMES[first:last + 1])
    return ", ".join(parts)


@lru_cache(maxsize=None)
def _nyse_holidays(year: int) -> frozenset[date]:
    return frozenset(holidays.NYSE(years=year).keys())


def is_holiday(day: date) -> bool:
    """NYSE holidays (D4, A16)."""
    return day in _nyse_holidays(day.year)


def is_trading_day(day: date, schedule: Schedule = DEFAULT_SCHEDULE) -> bool:
    """A day the schedule opens on (Monday to Friday by default) that isn't a holiday (R15.1-R15.3)."""
    return day.weekday() in schedule.trading_days and not is_holiday(day)


def _local(at_utc: datetime, tz: ZoneInfo) -> datetime:
    if at_utc.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime")
    return at_utc.astimezone(tz)


def is_open(at_utc: datetime, schedule: Schedule = DEFAULT_SCHEDULE, tz: ZoneInfo = MARKET_TZ) -> bool:
    """True if the market is open at this instant: forced open (D29), or a trading day and open <= time < close."""
    local = _local(at_utc, tz)
    if schedule.force_open:
        return True  # R15.7: beats the hours, the chosen days, and holidays
    if not is_trading_day(local.date(), schedule):
        return False
    return schedule.open_time <= local.time().replace(tzinfo=None) < schedule.close_time


def next_open(at_utc: datetime, schedule: Schedule = DEFAULT_SCHEDULE, tz: ZoneInfo = MARKET_TZ) -> datetime:
    """The next scheduled opening, strictly after `at_utc`, in market-local time (Force market open aside)."""
    local = _local(at_utc, tz)
    day = local.date()
    for _ in range(366 * 2):  # any chosen weekday comes round within a year, and not every one is a holiday
        candidate = datetime.combine(day, schedule.open_time, tzinfo=tz)
        if is_trading_day(day, schedule) and candidate > local:
            return candidate
        day += timedelta(days=1)
    raise RuntimeError("no trading day found within two years")


def market_status(at_utc: datetime, schedule: Schedule = DEFAULT_SCHEDULE, tz: ZoneInfo = MARKET_TZ) -> MarketStatus:
    """Open/closed, plus when it closes (if open) or next opens (if closed), for the Trade page (R15.6)
    and the Market Time card (R15.9). Forced open has no closing time (D29)."""
    local = _local(at_utc, tz)
    if schedule.force_open:
        return MarketStatus(True, None, None, forced_open=True)
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
    """The most recently saved market settings, or the default 9:00-17:00 Monday to Friday (AR05).

    The settings apply until changed; there are no dates (D21, D29). Pass the
    cursor of an open transaction to read inside it (nothing is committed then).
    """
    from app.db import get_db

    sql = ("SELECT open_time, close_time, trading_days, force_open FROM MarketSchedule"
           " ORDER BY updated_at DESC, scheduleID DESC LIMIT 1")
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
    return Schedule(_as_time(found["open_time"]), _as_time(found["close_time"]),
                    days_from_mask(found["trading_days"]), bool(found["force_open"]))


def save_schedule(admin_user_id: int, open_time: time | None, close_time: time | None,
                  trading_days=MON_TO_FRI, force_open: bool = False) -> None:
    """Change the market hours, trading days, and Force market open together (R15.3, R15.4, R15.7, R15.8, D29).

    An invalid schedule leaves the current one in place (R15.5).
    """
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
    days = set(trading_days or ())
    if not days:
        raise ValidationFailed("trading_days", "Select at least one trading day.")
    if not days <= ALL_DAYS:
        raise ValidationFailed("trading_days", "Choose trading days from Monday to Sunday.")
    with transaction() as cur:
        cur.execute(
            "INSERT INTO MarketSchedule (open_time, close_time, trading_days, force_open, updated_by)"
            " VALUES (%s, %s, %s, %s, %s)",
            (open_time, close_time, days_to_mask(days), bool(force_open), admin_user_id),
        )


def is_market_open(at_utc: datetime | None = None) -> bool:
    """Is the market open now (or at `at_utc`), using the saved schedule? Used by trading (R8.4, R9.3)."""
    from app import clock

    return is_open(at_utc or clock.now_utc(), current_schedule(), market_tz())


def current_status(at_utc: datetime | None = None) -> MarketStatus:
    """Open/closed and the next change, using the saved schedule (R15.6)."""
    from app import clock

    return market_status(at_utc or clock.now_utc(), current_schedule(), market_tz())
