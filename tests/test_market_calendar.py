"""Clock and market calendar (task 1.4; R15.1, R15.2, D4, D6, A16)."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app import clock
from app.services.market import (
    DEFAULT_SCHEDULE,
    MON_TO_FRI,
    Schedule,
    days_from_mask,
    days_short,
    days_text,
    days_to_mask,
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


# --- Trading days and Force market open (task 6.3; R15.3, R15.7, D29) ----------------------------------------
WEEKEND = Schedule(trading_days={5, 6})
FORCED = Schedule(force_open=True)


def test_default_schedule_is_monday_to_friday_and_not_forced():  # R15.1, AR05
    assert DEFAULT_SCHEDULE.trading_days == MON_TO_FRI == {0, 1, 2, 3, 4}
    assert not DEFAULT_SCHEDULE.force_open


def test_chosen_days_open_and_other_days_close():  # R15.3
    saturday, monday = az(2026, 9, 26, 12, 0), az(*MONDAY, 12, 0)
    assert is_open(saturday.astimezone(UTC), WEEKEND)
    assert not is_open(monday.astimezone(UTC), WEEKEND)
    assert is_trading_day(date(2026, 9, 27), WEEKEND) and not is_trading_day(date(*MONDAY), WEEKEND)
    assert not is_open(az(2026, 9, 26, 8, 59).astimezone(UTC), WEEKEND)      # the hours still apply
    assert not is_open(az(2026, 9, 26, 17, 0).astimezone(UTC), WEEKEND)


def test_holidays_still_close_a_chosen_day():  # R15.2, D29
    every_day = Schedule(trading_days=range(7))
    assert not is_open(az(2026, 11, 26, 12, 0).astimezone(UTC), every_day)   # Thanksgiving, a Thursday
    assert is_open(az(2026, 9, 26, 12, 0).astimezone(UTC), every_day)        # an ordinary Saturday


@pytest.mark.parametrize("moment", [
    az(*MONDAY, 3, 0),             # before the hours
    az(*MONDAY, 23, 59),           # after the hours
    az(2026, 9, 26, 12, 0),        # a Saturday
    az(2026, 11, 26, 12, 0),       # Thanksgiving
    az(2026, 12, 25, 0, 0),        # Christmas, midnight
])
def test_force_open_beats_hours_days_and_holidays(moment):  # R15.7
    assert is_open(moment.astimezone(UTC), FORCED)
    assert is_open(moment.astimezone(UTC), Schedule(trading_days={2}, force_open=True))


def test_market_status_while_forced_open_has_no_closing_time():  # R15.9, D29
    status = market_status(az(2026, 9, 26, 12, 0).astimezone(UTC), FORCED)
    assert status.is_open and status.forced_open and status.closes_at is None and status.next_open is None
    assert not market_status(az(*MONDAY, 10, 0).astimezone(UTC)).forced_open


def test_next_open_skips_days_that_are_not_chosen():  # R15.3
    # Monday after the close, with only Wednesday and Saturday chosen -> Wednesday 9:00
    assert next_open(az(*MONDAY, 17, 0).astimezone(UTC), Schedule(trading_days={2, 5})) == az(2026, 9, 30, 9, 0)
    # Friday evening with the weekend chosen -> Saturday 9:00
    assert next_open(az(2026, 9, 25, 18, 0).astimezone(UTC), WEEKEND) == az(2026, 9, 26, 9, 0)


@given(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2035, 12, 31), timezones=st.just(UTC)),
       st.frozensets(st.integers(0, 6), min_size=1))
def test_next_open_is_the_first_open_moment_on_any_chosen_days(moment, days):  # R15.3
    schedule = Schedule(trading_days=days)
    opening = next_open(moment, schedule)
    assert opening > moment
    assert opening.weekday() in days
    assert is_open(opening, schedule) and not is_open(opening - timedelta(microseconds=1), schedule)


@pytest.mark.parametrize("days", [set(), {7}, {-1}, {0, 9}])
def test_schedule_needs_one_or_more_real_days(days):  # R15.5
    with pytest.raises(ValueError):
        Schedule(trading_days=days)


@given(st.frozensets(st.integers(0, 6), min_size=1))
def test_days_survive_the_bit_mask(days):  # DV12
    mask = days_to_mask(days)
    assert 1 <= mask <= 127 and days_from_mask(mask) == days


def test_monday_to_friday_is_mask_31():  # DV12: migration 008's default
    assert days_to_mask(MON_TO_FRI) == 31 and days_from_mask(31) == MON_TO_FRI


@pytest.mark.parametrize("days, text, short", [
    ({0, 1, 2, 3, 4}, "Monday to Friday", "Mon–Fri"),
    (set(range(7)), "every day", "Every day"),
    ({0, 2, 4}, "Monday, Wednesday and Friday", "Mon, Wed, Fri"),
    ({5, 6}, "Saturday and Sunday", "Sat, Sun"),
    ({3}, "Thursday", "Thu"),
    ({0, 1, 2, 4}, "Monday to Wednesday and Friday", "Mon–Wed, Fri"),
    ({0, 1, 3, 4, 5, 6}, "Monday, Tuesday and Thursday to Sunday", "Mon, Tue, Thu–Sun"),
])
def test_days_in_words(days, text, short):  # R15.9, D29
    assert days_text(days) == text
    assert days_short(days) == short
