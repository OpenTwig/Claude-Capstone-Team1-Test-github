"""Clock and market calendar (task 1.4; R15.1, R15.2, D4, D6, A16)."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app import clock
from app.services.market import (
    DEFAULT_SCHEDULE,
    Schedule,
    is_holiday,
    is_open,
    is_trading_day,
    market_status,
    next_open,
)

AZ = ZoneInfo("America/Phoenix")
UTC = timezone.utc


def az(y, mo, d, h=0, mi=0, s=0, us=0):
    """An Arizona-local instant."""
    return datetime(y, mo, d, h, mi, s, us, tzinfo=AZ)


# 2026-09-28 is a Monday.
MONDAY = (2026, 9, 28)


@pytest.mark.parametrize(
    "moment, expected",
    [
        (az(*MONDAY, 8, 59, 59, 999999), False),   # R15.1: opens at 9:00
        (az(*MONDAY, 9, 0, 0), True),
        (az(*MONDAY, 12, 30), True),
        (az(*MONDAY, 16, 59, 59, 999999), True),
        (az(*MONDAY, 17, 0, 0), False),             # closes at 17:00 (exclusive)
        (az(*MONDAY, 23, 0), False),
    ],
)
def test_opening_and_closing_boundaries_in_arizona_time(moment, expected):
    assert is_open(moment.astimezone(UTC)) is expected


def test_arizona_is_utc_minus_7_all_year():  # D6: no daylight saving
    assert is_open(datetime(2026, 1, 12, 16, 0, tzinfo=UTC))       # 09:00 Arizona in winter
    assert is_open(datetime(2026, 7, 13, 16, 0, tzinfo=UTC))       # 09:00 Arizona in summer
    assert not is_open(datetime(2026, 7, 13, 15, 59, tzinfo=UTC))  # 08:59 Arizona


@pytest.mark.parametrize("day", [date(2026, 9, 26), date(2026, 9, 27)])  # Saturday, Sunday
def test_weekends_are_closed(day):
    assert not is_trading_day(day)
    assert not is_open(datetime.combine(day, time(12), tzinfo=AZ))


@pytest.mark.parametrize(
    "day, name",
    [
        (date(2026, 1, 1), "New Year's Day"),
        (date(2026, 4, 3), "Good Friday"),
        (date(2026, 7, 3), "Independence Day (observed)"),
        (date(2026, 11, 26), "Thanksgiving"),
        (date(2026, 12, 25), "Christmas"),
    ],
)
def test_nyse_holidays_are_closed(day, name):  # AR05, D4
    assert is_holiday(day), name
    assert not is_open(datetime.combine(day, time(12), tzinfo=AZ))


def test_ordinary_weekday_is_not_a_holiday():
    assert not is_holiday(date(2026, 7, 2))
    assert is_trading_day(date(2026, 7, 2))


def test_next_open_from_friday_close_is_monday_morning():
    friday_close = az(2026, 9, 25, 17, 0)
    assert next_open(friday_close.astimezone(UTC)) == az(*MONDAY, 9, 0)


def test_next_open_before_opening_is_the_same_morning():
    assert next_open(az(*MONDAY, 7, 30).astimezone(UTC)) == az(*MONDAY, 9, 0)


def test_next_open_skips_a_holiday():
    # Thursday 2026-04-02 after close -> Friday is Good Friday -> Monday 2026-04-06
    assert next_open(az(2026, 4, 2, 17, 30).astimezone(UTC)) == az(2026, 4, 6, 9, 0)


def test_next_open_while_open_is_the_following_trading_day():
    assert next_open(az(*MONDAY, 10, 0).astimezone(UTC)) == az(2026, 9, 29, 9, 0)


def test_market_status_while_open_and_closed():  # R15.6
    open_status = market_status(az(*MONDAY, 10, 0).astimezone(UTC))
    assert open_status.is_open and open_status.closes_at == az(*MONDAY, 17, 0) and open_status.next_open is None
    closed_status = market_status(az(2026, 9, 26, 10, 0).astimezone(UTC))
    assert not closed_status.is_open and closed_status.next_open == az(*MONDAY, 9, 0) and closed_status.closes_at is None


def test_a_custom_schedule_is_respected():  # R15.3 (schedule passed in; stored schedule is task 2.4)
    late = Schedule(open_time=time(10, 30), close_time=time(15, 0))
    assert not is_open(az(*MONDAY, 10, 0).astimezone(UTC), late)
    assert is_open(az(*MONDAY, 10, 30).astimezone(UTC), late)
    assert not is_open(az(*MONDAY, 15, 0).astimezone(UTC), late)


def test_schedule_close_must_be_after_open():  # R15.5
    with pytest.raises(ValueError):
        Schedule(open_time=time(17), close_time=time(9))
    with pytest.raises(ValueError):
        Schedule(open_time=time(9), close_time=time(9))


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError):
        is_open(datetime(2026, 9, 28, 12, 0))


@given(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2035, 12, 31), timezones=st.just(UTC)))
def test_next_open_is_the_first_open_moment_after_now(moment):
    opening = next_open(moment)
    assert opening > moment
    assert is_open(opening)
    assert not is_open(opening - timedelta(microseconds=1))
    status = market_status(moment)
    assert status.is_open == is_open(moment)


def test_clock_can_be_frozen_and_unfrozen():
    fixed = datetime(2026, 9, 28, 16, 0, tzinfo=UTC)
    clock.freeze(fixed)
    try:
        assert clock.now_utc() == fixed
        assert clock.now_local() == az(*MONDAY, 9, 0)
    finally:
        clock.unfreeze()
    assert abs(clock.now_utc() - datetime.now(UTC)) < timedelta(seconds=5)


def test_freeze_refuses_naive_datetimes():
    with pytest.raises(ValueError):
        clock.freeze(datetime(2026, 9, 28, 16, 0))


def test_fixed_clock_fixture(fixed_clock):
    fixed_clock(az(*MONDAY, 9, 30))
    assert clock.now_local() == az(*MONDAY, 9, 30)
    assert is_open(clock.now_utc())
