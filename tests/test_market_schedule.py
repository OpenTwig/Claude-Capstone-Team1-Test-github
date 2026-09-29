"""Stored market schedule (task 2.4; R15.3-R15.5, AR04, AR05, Q9)."""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.errors import Forbidden, ValidationFailed
from app.services import market
from tests.web import make_login_user

pytestmark = pytest.mark.db

AZ = ZoneInfo("America/Phoenix")


def az(h, m=0, day=28):  # 2026-09-28 is a Monday
    return datetime(2026, 9, day, h, m, tzinfo=AZ)


def count(db):
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM MarketSchedule")
        n = cur.fetchone()["n"]
    db.commit()
    return n


@pytest.fixture
def admin(db, app_ctx):
    return make_login_user(db, admin=True)


def test_default_hours_apply_until_an_admin_saves_some(app_ctx):  # AR05
    assert market.current_schedule() == market.Schedule(time(9), time(17))
    assert not market.is_market_open(az(8, 59))
    assert market.is_market_open(az(9, 0))
    assert not market.is_market_open(az(17, 0))


def test_saved_hours_change_every_market_open_check(admin):  # R15.3, R15.4
    market.save_schedule(admin, time(10, 30), time(15, 0))
    assert market.current_schedule() == market.Schedule(time(10, 30), time(15, 0))
    assert not market.is_market_open(az(10, 0))
    assert market.is_market_open(az(10, 30))
    assert market.is_market_open(az(14, 59))
    assert not market.is_market_open(az(15, 0))
    status = market.current_status(az(12, 0))
    assert status.is_open and status.closes_at == az(15, 0)
    assert market.current_status(az(16, 0)).next_open == az(10, 30, day=29)


def test_the_most_recent_schedule_wins(admin):
    market.save_schedule(admin, time(10), time(15))
    market.save_schedule(admin, time(8), time(12))
    assert market.current_schedule() == market.Schedule(time(8), time(12))


def test_holidays_and_weekends_stay_closed_with_custom_hours(admin):  # AR05
    market.save_schedule(admin, time(6), time(20))
    assert not market.is_market_open(datetime(2026, 11, 26, 12, 0, tzinfo=AZ))  # Thanksgiving
    assert not market.is_market_open(datetime(2026, 9, 26, 12, 0, tzinfo=AZ))   # Saturday


@pytest.mark.parametrize("open_time, close_time, field", [
    (time(17), time(9), "close_time"),
    (time(9), time(9), "close_time"),
    (None, time(17), "open_time"),
    (time(9), None, "close_time"),
])
def test_invalid_hours_are_rejected_and_the_old_schedule_stays(db, admin, open_time, close_time, field):  # R15.5
    market.save_schedule(admin, time(10), time(15))
    with pytest.raises(ValidationFailed) as exc:
        market.save_schedule(admin, open_time, close_time)
    assert exc.value.field == field
    assert market.current_schedule() == market.Schedule(time(10), time(15))
    assert count(db) == 1


def test_saved_hours_record_who_changed_them_and_have_no_dates(db, admin):  # D21
    market.save_schedule(admin, time(11), time(13))
    with db.cursor() as cur:
        cur.execute("SELECT * FROM MarketSchedule")
        saved = cur.fetchone()
    db.commit()
    assert saved["updated_by"] == admin and "start_date" not in saved and "end_date" not in saved
    assert market.is_market_open(az(12, 0))  # the new hours apply from now on, every trading day


def test_only_administrators_can_change_the_hours(db, admin):  # R2.4, AR04
    customer = make_login_user(db)
    with pytest.raises(Forbidden):
        market.save_schedule(customer, time(10), time(15))
    assert count(db) == 0


def test_time_columns_are_read_back_as_times_of_day():
    from datetime import timedelta

    assert market._as_time(timedelta(hours=9, minutes=30, seconds=5)) == time(9, 30, 5)
    assert market._as_time(time(16, 45)) == time(16, 45)
