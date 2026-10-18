"""Price generator worker (task 3.3; R16, R7.8, A7, A8, A17, D12, D23; P6)."""
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.db import connect
from app.services import price_generator
from app.services.price_generator import TickState, lock_name, snapshot_values, tick
from app.services.pricing import PriceModel
from tests.factories import make_portfolio, make_stock, make_user
from tests.web import login, make_login_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=AZ)        # Monday, market open
FRIDAY = date(2026, 9, 25)
AFTER_CLOSE = datetime(2026, 9, 28, 17, 0, tzinfo=AZ)
SATURDAY = datetime(2026, 9, 26, 12, 0, tzinfo=AZ)


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def rows(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchall()
    db.commit()
    return found


def price(db, issuer_id):
    return one(db, "SELECT current_price FROM Issuer WHERE issuerID = %s", (issuer_id,))["current_price"]


def day_row(db, issuer_id, day=NOW.date()):
    return one(db, "SELECT open_price, close_price FROM StockPrice WHERE issuerID = %s AND price_date = %s",
               (issuer_id, day))


@pytest.fixture
def model(web_app):
    return PriceModel.from_settings(web_app.config["SETTINGS"])


@pytest.fixture
def gen(db, app_ctx, fixed_clock):
    """Two listed stocks with Friday's closes recorded, and a delisted one."""
    fixed_clock(NOW)
    stocks = {"ACME": make_stock(db, symbol="ACME", price=D("50.00")),
              "CHEAP": make_stock(db, symbol="CHEAP", price=D("1.00"))}
    for issuer_id, close in ((stocks["ACME"], "50.00"), (stocks["CHEAP"], "1.00")):
        one(db, "INSERT INTO StockPrice (issuerID, price_date, open_price, close_price) VALUES (%s, %s, %s, %s)",
            (issuer_id, FRIDAY, close, close))
    stocks["GONE"] = make_stock(db, symbol="GONE", price=D("20.00"), is_listed=False)
    return stocks


# --- tick() (R16.2-R16.4, R16.7, A8, P6) -------------------------------------------------------------

def test_a_tick_moves_every_listed_stock_and_records_the_close(db, gen, model, rng):  # R16.2, P6
    moved = tick(TickState(), NOW.astimezone(timezone.utc), rng, model)
    assert moved == 2
    for symbol, before in (("ACME", D("50.00")), ("CHEAP", D("1.00"))):
        now_price = price(db, gen[symbol])
        assert now_price >= D("0.01")
        assert abs(now_price - before) <= before * D("0.01") + D("0.01")    # within the 1% cap, plus a cent
        assert day_row(db, gen[symbol])["close_price"] == now_price          # today's close follows (SR02)


def test_first_tick_of_the_day_opens_at_the_previous_close(db, gen, model, rng):  # R16.4, A8, R16.7
    state = TickState()
    tick(state, NOW.astimezone(timezone.utc), rng, model)
    assert day_row(db, gen["ACME"])["open_price"] == D("50.00")            # Friday's close
    for minutes in (1, 2, 3):
        tick(state, (NOW + timedelta(minutes=minutes)).astimezone(timezone.utc), rng, model)
    today = day_row(db, gen["ACME"])
    assert today["open_price"] == D("50.00")                                # later ticks keep the open
    assert today["close_price"] == price(db, gen["ACME"])
    counts = rows(db, "SELECT price_date, COUNT(*) AS n FROM StockPrice WHERE issuerID = %s GROUP BY price_date",
                  (gen["ACME"],))
    assert {r["price_date"]: r["n"] for r in counts} == {FRIDAY: 1, NOW.date(): 1}   # one row per day (R16.7)


def test_delisted_stocks_do_not_move(db, gen, model, rng):  # D20
    tick(TickState(), NOW.astimezone(timezone.utc), rng, model)
    assert price(db, gen["GONE"]) == D("20.00") and day_row(db, gen["GONE"]) is None


def test_prices_never_fall_below_one_cent(db, app_ctx, fixed_clock):  # R16.3, P6
    fixed_clock(NOW)
    penny = make_stock(db, symbol="PENNY", price=D("0.01"))
    wild = PriceModel(daily_volatility=D("5"), max_tick_change=D("0.5"), tick_max_seconds=120)  # absurdly volatile
    state, rng = TickState(), random.Random(7)
    for minute in range(40):
        tick(state, (NOW + timedelta(minutes=minute)).astimezone(timezone.utc), rng, wild)
        assert price(db, penny) >= D("0.01")


def test_cheap_stocks_do_not_freeze(db, gen, model):  # design: full precision kept in memory
    state, rng, seen = TickState(), random.Random(20260928), set()
    for step in range(60):  # 75-second ticks: each moves a $1 stock by far less than a cent
        tick(state, (NOW + timedelta(seconds=75 * step)).astimezone(timezone.utc), rng, model)
        seen.add(price(db, gen["CHEAP"]))
    assert len(seen) > 1
    assert state.exact[gen["CHEAP"]] != D("1.00")  # the unrounded price is what moves


def test_a_price_changed_elsewhere_is_picked_up(db, gen, model, rng):  # resync rule
    state = TickState()
    tick(state, NOW.astimezone(timezone.utc), rng, model)
    one(db, "UPDATE Issuer SET current_price = 80.00 WHERE issuerID = %s", (gen["ACME"],))
    tick(state, (NOW + timedelta(minutes=1)).astimezone(timezone.utc), rng, model)
    assert abs(price(db, gen["ACME"]) - D("80.00")) <= D("0.81")


def test_a_tick_records_the_heartbeat(db, gen, model, rng):  # A17
    tick(TickState(), NOW.astimezone(timezone.utc), rng, model)
    beat = one(db, "SELECT state_value, updated_at FROM SystemState WHERE state_key = 'last_price_tick'")
    assert beat["updated_at"] == datetime(2026, 9, 28, 17, 0)  # 10:00 Arizona = 17:00 UTC
    assert beat["state_value"] == "2026-09-28T17:00:00.000"


# --- The run loop (R16.1, R16.5, R16.6) -----------------------------------------------------------------

def passes(n):
    left = [n]

    def keep_going():
        left[0] -= 1
        return left[0] >= 0
    return keep_going


def test_run_ticks_while_the_market_is_open(db, web_app, gen, rng):  # R16.1, A7
    waits = []
    assert price_generator.run(web_app, rng=rng, sleep=waits.append, keep_going=passes(3)) is True
    assert len(waits) == 3 and all(30 <= w <= 120 for w in waits)  # random intervals within A7
    assert day_row(db, gen["ACME"]) is not None
    assert one(db, "SELECT COUNT(*) AS n FROM SystemState WHERE state_key = 'last_price_tick'")["n"] == 1


@pytest.mark.parametrize("moment", [SATURDAY, datetime(2026, 9, 28, 8, 59, tzinfo=AZ), AFTER_CLOSE])
def test_run_does_nothing_to_prices_while_the_market_is_closed(db, web_app, gen, rng, fixed_clock, moment):  # R16.5
    fixed_clock(moment)
    price_generator.run(web_app, rng=rng, sleep=lambda s: None, keep_going=passes(3))
    assert (price(db, gen["ACME"]), price(db, gen["CHEAP"])) == (D("50.00"), D("1.00"))
    assert one(db, "SELECT COUNT(*) AS n FROM StockPrice WHERE price_date > %s", (FRIDAY,))["n"] == 0
    assert one(db, "SELECT COUNT(*) AS n FROM SystemState")["n"] == 0


def test_a_second_generator_exits_while_the_lock_is_held(db, web_app, gen, rng):  # R16.6, D12
    settings = web_app.config["SETTINGS"]
    holder = connect(settings)
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0) AS got", (lock_name(settings.db_name),))
            assert cur.fetchone()["got"] == 1
        waits = []
        assert price_generator.run(web_app, rng=rng, sleep=waits.append, keep_going=passes(3)) is False
        assert waits == [] and price(db, gen["ACME"]) == D("50.00")
    finally:
        holder.close()
    assert price_generator.run(web_app, rng=rng, sleep=lambda s: None, keep_going=passes(1)) is True  # lock freed


def test_the_lock_is_released_when_the_generator_stops(db, web_app, gen, rng):
    price_generator.run(web_app, rng=rng, sleep=lambda s: None, keep_going=passes(1))
    settings = web_app.config["SETTINGS"]
    other = connect(settings)
    try:
        with other.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0) AS got", (lock_name(settings.db_name),))
            assert cur.fetchone()["got"] == 1
    finally:
        other.close()


def test_lock_names_are_per_database():
    assert lock_name("trading_db") != lock_name("trading_test")


def test_a_failed_pass_does_not_stop_the_generator(db, web_app, gen, rng, monkeypatch):
    calls = []
    real = price_generator.tick

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database went away")
        return real(*args, **kwargs)

    monkeypatch.setattr(price_generator, "tick", flaky)
    assert price_generator.run(web_app, rng=rng, sleep=lambda s: None, keep_going=passes(2)) is True
    assert len(calls) == 2 and day_row(db, gen["ACME"]) is not None


def test_the_cli_command_refuses_to_start_a_second_generator(db, web_app, gen):  # R16.6
    settings = web_app.config["SETTINGS"]
    holder = connect(settings)
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0)", (lock_name(settings.db_name),))
        result = web_app.test_cli_runner().invoke(args=["price-generator"])
    finally:
        holder.close()
    assert result.exit_code != 0 and "Another price generator is already running" in result.output


# --- Daily value snapshot (D23) -----------------------------------------------------------------------

@pytest.fixture
def holdings(db, gen):
    """Portfolio A: $100.00 cash, 2.5 ACME and 0.333333 CHEAP. Portfolio B: $50.00 cash only."""
    user = make_user(db)
    a = make_portfolio(db, user, cash_balance=D("100.00"))
    b = make_portfolio(db, user, cash_balance=D("50.00"))
    one(db, "UPDATE Issuer SET current_price = 30.00 WHERE issuerID = %s", (gen["ACME"],))
    one(db, "UPDATE Issuer SET current_price = 10.00 WHERE issuerID = %s", (gen["CHEAP"],))
    for issuer_id, quantity in ((gen["ACME"], "2.5"), (gen["CHEAP"], "0.333333")):
        one(db, "INSERT INTO Inventory (portfolioID, issuerID, quantity, average_cost) VALUES (%s, %s, %s, 1.00)",
            (a, issuer_id, quantity))
    return a, b


def snapshots(db):
    return {(r["portfolioID"], r["value_date"]): (r["cash"], r["total_value"])
            for r in rows(db, "SELECT * FROM PortfolioValueDaily")}


def test_after_the_close_each_portfolio_value_is_saved_once(db, holdings, fixed_clock):  # D23
    a, b = holdings
    fixed_clock(AFTER_CLOSE)
    assert snapshot_values(AFTER_CLOSE.astimezone(timezone.utc)) == 2
    # A: 100.00 + 2.5 x 30.00 (75.00) + 0.333333 x 10.00 (3.33333 -> 3.33) = 178.33
    assert snapshots(db) == {(a, NOW.date()): (D("100.00"), D("178.33")), (b, NOW.date()): (D("50.00"), D("50.00"))}
    later = (AFTER_CLOSE + timedelta(hours=3)).astimezone(timezone.utc)
    assert snapshot_values(later) == 0                                   # a rerun changes nothing
    assert len(snapshots(db)) == 2


@pytest.mark.parametrize("moment", [NOW, datetime(2026, 9, 28, 16, 59, tzinfo=AZ), SATURDAY,
                                    datetime(2026, 11, 26, 18, 0, tzinfo=AZ)])  # open, before close, weekend, holiday
def test_no_snapshot_unless_a_trading_day_has_closed(db, holdings, moment):  # D23
    assert snapshot_values(moment.astimezone(timezone.utc)) == 0 and snapshots(db) == {}


def test_the_generator_saves_the_snapshot_after_the_close(db, web_app, holdings, fixed_clock, rng):  # D23
    fixed_clock(AFTER_CLOSE)
    price_generator.run(web_app, rng=rng, sleep=lambda s: None, keep_going=passes(3))
    assert len(snapshots(db)) == 2


# --- "Prices may be delayed" (R7.8, A17) --------------------------------------------------------------

@pytest.fixture
def signed_in(db, client, fixed_clock):
    fixed_clock(NOW)
    make_login_user(db, email="casey@example.test")
    login(client, "casey@example.test", "correct horse battery")
    return client


def beat(db, seconds_ago):
    at = (NOW - timedelta(seconds=seconds_ago)).astimezone(timezone.utc).replace(tzinfo=None)
    one(db, "INSERT INTO SystemState (state_key, state_value, updated_at) VALUES ('last_price_tick', %s, %s)",
        (at.isoformat(), at))


@pytest.mark.parametrize("seconds_ago, shown", [(299, False), (300, False), (301, True), (None, True)])
def test_stale_banner_while_the_market_is_open(db, signed_in, seconds_ago, shown):  # R7.8, A17
    if seconds_ago is not None:
        beat(db, seconds_ago)
    html = signed_in.get("/account").get_data(as_text=True)
    assert ("Prices may be delayed" in html) is shown


def test_no_stale_banner_while_the_market_is_closed(db, signed_in, fixed_clock):  # R7.8
    beat(db, 3600)
    fixed_clock(SATURDAY)
    assert "Prices may be delayed" not in signed_in.get("/account").get_data(as_text=True)


def test_no_stale_banner_on_the_login_page(web_app, fixed_clock):
    fixed_clock(NOW)
    assert "Prices may be delayed" not in web_app.test_client().get("/signin").get_data(as_text=True)


def test_a_failing_stale_check_never_breaks_the_page(db, signed_in, monkeypatch):
    def broken(*args):
        raise RuntimeError("database went away")

    monkeypatch.setattr(price_generator, "prices_stale", broken)
    response = signed_in.get("/account")
    assert response.status_code == 200 and "Prices may be delayed" not in response.get_data(as_text=True)


# --- Trading days and the Market Control (task 6.3; R15.4, R15.7, D23, D29) ---------------------------------

def set_schedule(db, trading_days=31, override="Schedule"):
    one(db, "INSERT INTO MarketSchedule (open_time, close_time, trading_days, market_override, updated_by)"
            " VALUES ('09:00', '17:00', %s, %s, %s)", (trading_days, override, make_user(db)))


def test_run_ticks_on_a_weekend_while_forced_open(db, web_app, gen, rng, fixed_clock):  # R15.4, R15.7
    set_schedule(db, override="Open")
    fixed_clock(SATURDAY)
    price_generator.run(web_app, rng=rng, sleep=lambda s: None, keep_going=passes(3))
    assert day_row(db, gen["ACME"], SATURDAY.date()) is not None
    assert one(db, "SELECT COUNT(*) AS n FROM SystemState WHERE state_key = 'last_price_tick'")["n"] == 1


def test_run_ticks_on_a_chosen_weekend_day(db, web_app, gen, rng, fixed_clock):  # R15.3, R15.4
    set_schedule(db, trading_days=(1 << 5) | (1 << 6))
    fixed_clock(SATURDAY)
    price_generator.run(web_app, rng=rng, sleep=lambda s: None, keep_going=passes(3))
    assert day_row(db, gen["ACME"], SATURDAY.date()) is not None


def test_run_moves_no_prices_during_the_hours_while_forced_closed(db, web_app, gen, rng, fixed_clock):  # R15.7, R16.5
    set_schedule(db, override="Closed")
    fixed_clock(NOW)                                          # Monday 10:00, normally open
    price_generator.run(web_app, rng=rng, sleep=lambda s: None, keep_going=passes(3))
    assert (price(db, gen["ACME"]), price(db, gen["CHEAP"])) == (D("50.00"), D("1.00"))
    assert day_row(db, gen["ACME"]) is None
    assert one(db, "SELECT COUNT(*) AS n FROM SystemState")["n"] == 0


def test_no_snapshot_while_forced_open(db, holdings):  # D23, D29: prices are still moving
    set_schedule(db, override="Open")
    assert snapshot_values(AFTER_CLOSE.astimezone(timezone.utc)) == 0 and snapshots(db) == {}


def test_snapshot_still_saved_after_the_close_while_forced_closed(db, holdings):  # D23, D29
    set_schedule(db, override="Closed")
    assert snapshot_values(AFTER_CLOSE.astimezone(timezone.utc)) == 2


def test_snapshot_after_the_close_of_a_chosen_weekend_day(db, holdings):  # D23, D29
    set_schedule(db, trading_days=1 << 5)                     # Saturdays only
    saturday_evening = SATURDAY.replace(hour=18)
    assert snapshot_values(saturday_evening.astimezone(timezone.utc)) == 2
    assert snapshot_values(AFTER_CLOSE.astimezone(timezone.utc)) == 0   # Monday is no longer a trading day
