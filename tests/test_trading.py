"""Trading service (task 3.2; R8, R9, A3, A4, D11, D16, D19, D20; properties P3, P5, P7, P8, P9, P18; N2.5)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pymysql
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.db import retry_once_on_lock_conflict
from app.errors import (
    Forbidden,
    InsufficientFunds,
    InsufficientShares,
    MarketClosed,
    NotEnoughSharesAvailable,
    NotFound,
    SymbolNotFound,
    ValidationFailed,
)
from app.money import MAX_MONEY
from app.services import trading
from app.services.types import OrderRequest
from tests.factories import make_admin, make_portfolio, make_stock, make_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=AZ)             # Monday, market open (09:00-17:00)
AFTER_CLOSE = datetime(2026, 9, 28, 17, 0, tzinfo=AZ)     # closing time counts as closed
SATURDAY = datetime(2026, 9, 26, 12, 0, tzinfo=AZ)
THANKSGIVING = datetime(2026, 11, 26, 12, 0, tzinfo=AZ)   # NYSE holiday


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def cash(db, portfolio_id):
    return one(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (portfolio_id,))["cash_balance"]


def position(db, portfolio_id, issuer_id):
    return one(db, "SELECT quantity, average_cost FROM Inventory WHERE portfolioID = %s AND issuerID = %s",
               (portfolio_id, issuer_id))


def supply(db, issuer_id):
    return one(db, "SELECT shares_available FROM Issuer WHERE issuerID = %s", (issuer_id,))["shares_available"]


def set_price(db, issuer_id, price):
    one(db, "UPDATE Issuer SET current_price = %s WHERE issuerID = %s", (price, issuer_id))


def counts(db):
    return tuple(one(db, f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("`Transaction`", "History", "Inventory"))


@pytest.fixture
def mkt(db, app_ctx, fixed_clock):
    """A customer with $1,000.00 in a portfolio, and ACME at $30.00 with 1,000 shares available."""
    fixed_clock(NOW)
    user = make_user(db)
    return type("Market", (), {
        "user": user,
        "portfolio": make_portfolio(db, user, cash_balance=D("1000.00")),
        "stock": make_stock(db, symbol="ACME", company_name="Acme Corp", price=D("30.00"),
                            shares_available=D("1000")),
    })


def order(mkt, action, unit, amount, symbol="ACME", **kwargs):
    return OrderRequest(portfolio_id=mkt.portfolio, symbol=symbol, action=action, unit=unit, amount=D(amount), **kwargs)


def buy(mkt, unit, amount, **kwargs):
    return trading.execute_order(mkt.user, order(mkt, "Buy", unit, amount, **kwargs))


def sell(mkt, unit, amount, **kwargs):
    return trading.execute_order(mkt.user, order(mkt, "Sell", unit, amount, **kwargs))


def unchanged(db, mkt, cash_balance=D("1000.00"), shares=D("1000"), rows=(0, 0, 0)):
    assert cash(db, mkt.portfolio) == cash_balance
    assert supply(db, mkt.stock) == shares
    assert counts(db) == rows


# --- Hand-computed golden cases (A3, R8.7, R9.6) --------------------------------------------------------

def test_dollars_buy_that_does_not_divide_evenly(db, mkt):  # R8.7, A3
    result = buy(mkt, "Dollars", "100.00")
    # 100 / 30 = 3.3333333... -> 3.333333 shares (rounded down); 3.333333 x 30 = 99.99999 -> $100.00
    assert (result.quantity, result.price, result.total) == (D("3.333333"), D("30.00"), D("100.00"))
    assert result.cash_balance == D("900.00") == cash(db, mkt.portfolio)
    assert position(db, mkt.portfolio, mkt.stock) == {"quantity": D("3.333333"), "average_cost": D("30.00")}
    assert supply(db, mkt.stock) == D("996.666667")  # 1000 - 3.333333 (D16)


def test_dollars_buy_of_an_expensive_stock_leaves_change(db, mkt):  # P7
    set_price(db, mkt.stock, D("70000.00"))
    result = buy(mkt, "Dollars", "100.00")
    # 100 / 70000 = 0.00142857... -> 0.001428 shares; 0.001428 x 70000 = $99.96 (never more than $100)
    assert (result.quantity, result.total) == (D("0.001428"), D("99.96"))
    assert cash(db, mkt.portfolio) == D("900.04")


def test_shares_buy(db, mkt):  # R8.7, A3
    result = buy(mkt, "Shares", "2.5")
    assert (result.quantity, result.total, result.cash_balance) == (D("2.5"), D("75.00"), D("925.00"))
    assert supply(db, mkt.stock) == D("997.5")


def test_a_second_buy_updates_the_average_cost(db, mkt):  # SR07, P8
    buy(mkt, "Shares", "10")                    # 10 @ 30.00 = 300.00
    set_price(db, mkt.stock, D("36.00"))
    buy(mkt, "Shares", "5")                     # 5 @ 36.00 = 180.00
    assert position(db, mkt.portfolio, mkt.stock) == {"quantity": D("15"), "average_cost": D("32.00")}  # 480 / 15
    assert cash(db, mkt.portfolio) == D("520.00")


def test_average_cost_rounds_half_up_to_the_cent(db, mkt):  # P8
    set_price(db, mkt.stock, D("10.00"))
    buy(mkt, "Shares", "3")                     # 3 @ 10.00
    set_price(db, mkt.stock, D("10.01"))
    buy(mkt, "Shares", "1")                     # 1 @ 10.01 -> (30 + 10.01) / 4 = 10.0025 -> 10.00
    assert position(db, mkt.portfolio, mkt.stock)["average_cost"] == D("10.00")
    set_price(db, mkt.stock, D("10.03"))
    buy(mkt, "Shares", "4")                     # (4 x 10.00 + 4 x 10.03) / 8 = 10.015 -> 10.02
    assert position(db, mkt.portfolio, mkt.stock)["average_cost"] == D("10.02")


def test_partial_sell_keeps_the_average_cost(db, mkt):  # R9.6
    buy(mkt, "Shares", "10")
    set_price(db, mkt.stock, D("40.00"))
    result = sell(mkt, "Shares", "4")
    assert (result.quantity, result.price, result.total) == (D("4"), D("40.00"), D("160.00"))
    assert result.cash_balance == D("860.00") == cash(db, mkt.portfolio)   # 1000 - 300 + 160
    assert position(db, mkt.portfolio, mkt.stock) == {"quantity": D("6"), "average_cost": D("30.00")}
    assert supply(db, mkt.stock) == D("994")                              # sold shares come back (CR08)


def test_full_sell_deletes_the_position(db, mkt):  # R9.6, P3
    buy(mkt, "Dollars", "100.00")
    result = sell(mkt, "Shares", "3.333333")
    assert result.total == D("100.00") and cash(db, mkt.portfolio) == D("1000.00")
    assert position(db, mkt.portfolio, mkt.stock) is None
    assert supply(db, mkt.stock) == D("1000")


def test_dollars_sell(db, mkt):  # R9.2, A3
    buy(mkt, "Shares", "10")
    result = sell(mkt, "Dollars", "100.00")
    assert (result.quantity, result.total) == (D("3.333333"), D("100.00"))
    assert position(db, mkt.portfolio, mkt.stock)["quantity"] == D("6.666667")


def test_trade_records_a_filled_transaction_and_history(db, mkt):  # R8.7, SR08, SR10, R17.1
    result = buy(mkt, "Shares", "2")
    t = one(db, "SELECT * FROM `Transaction` WHERE transactionID = %s", (result.transaction_id,))
    assert (t["portfolioID"], t["issuerID"], t["type"], t["quantity"], t["price_per_share"], t["total_amount"]) == (
        mkt.portfolio, mkt.stock, "Buy", D("2"), D("30.00"), D("60.00"))
    assert (t["status"], t["order_type"], t["timing"]) == ("Filled", "Market", "Day Only")
    assert t["timestamp"] == NOW.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    h = one(db, "SELECT * FROM History WHERE transactionID = %s", (result.transaction_id,))
    assert (h["portfolioID"], h["action"], h["transferID"], h["timestamp"]) == (mkt.portfolio, "Buy", None, t["timestamp"])
    assert (result.symbol, result.action) == ("ACME", "Buy")


# --- Rejections change nothing (P5, P9) -------------------------------------------------------------

@pytest.mark.parametrize("moment", [AFTER_CLOSE, SATURDAY, THANKSGIVING])
@pytest.mark.parametrize("action", ["Buy", "Sell"])
def test_market_closed_refuses_orders(db, mkt, fixed_clock, moment, action):  # R8.4, R9.3, P5
    buy(mkt, "Shares", "1")  # something to sell
    before = (cash(db, mkt.portfolio), supply(db, mkt.stock), counts(db))
    fixed_clock(moment)
    for attempt in (trading.preview_order, trading.execute_order):
        with pytest.raises(MarketClosed) as exc:
            attempt(mkt.user, order(mkt, action, "Shares", "1"))
        assert exc.value.message == "Market is closed"
    assert (cash(db, mkt.portfolio), supply(db, mkt.stock), counts(db)) == before


def test_saved_market_hours_are_used(db, mkt):  # R15.3, R8.4
    one(db, "INSERT INTO MarketSchedule (open_time, close_time, updated_by) VALUES ('11:00', '15:00', %s)",
        (make_user(db),))
    with pytest.raises(MarketClosed):  # 10:00 is before the saved 11:00 open
        buy(mkt, "Shares", "1")


def test_insufficient_funds(db, mkt):  # R8.5, P2
    for attempt in (trading.preview_order, trading.execute_order):
        with pytest.raises(InsufficientFunds) as exc:
            attempt(mkt.user, order(mkt, "Buy", "Dollars", "1000.01"))
        assert exc.value.message == "Insufficient funds"
    unchanged(db, mkt)
    assert buy(mkt, "Dollars", "1000.00").cash_balance == D("0.00")  # exactly all of it is fine


def test_selling_more_than_owned_is_refused(db, mkt):  # R9.4
    buy(mkt, "Shares", "2")
    for attempt in (trading.preview_order, trading.execute_order):
        with pytest.raises(InsufficientShares) as exc:
            attempt(mkt.user, order(mkt, "Sell", "Shares", "2.000001"))
        assert exc.value.message == "Quantity exceeds shares owned"
    assert position(db, mkt.portfolio, mkt.stock)["quantity"] == D("2")


def test_selling_a_stock_not_held_is_refused(db, mkt):  # R9.4
    with pytest.raises(InsufficientShares):
        sell(mkt, "Shares", "1")
    unchanged(db, mkt)


def test_buying_more_than_the_supply_is_refused(db, mkt):  # R8.11, D16
    one(db, "UPDATE Issuer SET shares_available = 5 WHERE issuerID = %s", (mkt.stock,))
    for request in (order(mkt, "Buy", "Shares", "5.000001"), order(mkt, "Buy", "Dollars", "151.00")):
        for attempt in (trading.preview_order, trading.execute_order):
            with pytest.raises(NotEnoughSharesAvailable) as exc:
                attempt(mkt.user, request)
            assert exc.value.message == "Not enough shares available"
    unchanged(db, mkt, shares=D("5"))
    buy(mkt, "Shares", "5")  # the whole supply is fine
    assert supply(db, mkt.stock) == D("0")


def test_unknown_symbol(db, mkt):  # R8.2
    for attempt in (trading.preview_order, trading.execute_order):
        with pytest.raises(SymbolNotFound):
            attempt(mkt.user, order(mkt, "Buy", "Shares", "1", symbol="NOPE"))
    with pytest.raises(SymbolNotFound):
        trading.quote(mkt.user, "bad symbol!")


@pytest.mark.parametrize("field, overrides", [
    ("action", {"action": "Hold"}),
    ("unit", {"unit": "Euros"}),
    ("order_type", {"order_type": "Limit"}),     # A4
    ("timing", {"timing": "Good Till Cancelled"}),  # A4
])
def test_invalid_order_fields(db, mkt, field, overrides):  # A4, N2.2
    request = OrderRequest(**{**order(mkt, "Buy", "Shares", "1").__dict__, **overrides})
    for attempt in (trading.preview_order, trading.execute_order):
        with pytest.raises(ValidationFailed) as exc:
            attempt(mkt.user, request)
        assert exc.value.field == field
    unchanged(db, mkt)


@pytest.mark.parametrize("unit, amount", [
    ("Dollars", D("0")), ("Dollars", D("-1")), ("Dollars", D("1.001")), ("Dollars", D("NaN")),
    ("Dollars", MAX_MONEY + D("0.01")), ("Dollars", 10.5),
    ("Shares", D("0")), ("Shares", D("0.0000001")), ("Shares", D("Infinity")), ("Shares", D("1E+13")),
    ("Shares", D("0.000001")),        # total $0.00003 rounds to $0.00
    ("Shares", D("10000000")),        # total $300,000,000 is over the maximum
])
def test_invalid_amounts(db, mkt, unit, amount):  # R8.3, A3, N2.2
    request = OrderRequest(mkt.portfolio, "ACME", "Buy", unit, amount)
    with pytest.raises(ValidationFailed) as exc:
        trading.execute_order(mkt.user, request)
    assert exc.value.field == "amount"
    unchanged(db, mkt)


def test_dollar_amount_too_small_for_one_share_increment(db, mkt):  # A3
    set_price(db, mkt.stock, D("70000.00"))
    with pytest.raises(ValidationFailed, match="less than 0.000001 of a share"):
        buy(mkt, "Dollars", "0.06")  # 0.06 / 70000 = 0.00000085... -> 0.000000 shares
    unchanged(db, mkt)


def test_someone_elses_portfolio_is_not_found(db, mkt):  # R2.5
    stranger = make_user(db)
    theirs = make_portfolio(db, stranger, cash_balance=D("5000.00"))
    request = OrderRequest(theirs, "ACME", "Buy", "Shares", D("1"))
    for attempt in (trading.preview_order, trading.execute_order):
        with pytest.raises(NotFound):
            attempt(mkt.user, request)
    with pytest.raises(NotFound):
        trading.quote(mkt.user, "ACME", theirs)
    assert cash(db, theirs) == D("5000.00") and counts(db) == (0, 0, 0)


def test_administrators_cannot_trade(db, mkt):  # D19
    _, admin = make_admin(db)
    their_portfolio = make_portfolio(db, admin, cash_balance=D("1000.00"))  # can't happen through the app
    request = OrderRequest(their_portfolio, "ACME", "Buy", "Shares", D("1"))
    for attempt in (trading.preview_order, trading.execute_order):
        with pytest.raises(Forbidden):
            attempt(admin, request)
    assert counts(db) == (0, 0, 0)


# --- Delisted stocks (D20) ---------------------------------------------------------------------------

def delist(db, issuer_id):
    one(db, "UPDATE Issuer SET is_listed = FALSE WHERE issuerID = %s", (issuer_id,))


def test_a_delisted_stock_cannot_be_bought(db, mkt):  # D20, R8.2
    delist(db, mkt.stock)
    for attempt in (trading.preview_order, trading.execute_order):
        with pytest.raises(SymbolNotFound):
            attempt(mkt.user, order(mkt, "Buy", "Shares", "1"))
    with pytest.raises(SymbolNotFound):
        trading.quote(mkt.user, "ACME", mkt.portfolio)  # not held here
    unchanged(db, mkt)


def test_a_holder_can_still_quote_and_sell_a_delisted_stock(db, mkt):  # R9.10, D20
    buy(mkt, "Shares", "3")
    delist(db, mkt.stock)
    assert trading.quote(mkt.user, "ACME", mkt.portfolio).shares_owned == D("3")
    with pytest.raises(SymbolNotFound):
        buy(mkt, "Shares", "1")  # but can't add to it
    preview = trading.preview_order(mkt.user, order(mkt, "Sell", "Shares", "3"))
    assert preview.total == D("90.00")
    sell(mkt, "Shares", "3")
    assert position(db, mkt.portfolio, mkt.stock) is None and cash(db, mkt.portfolio) == D("1000.00")


def test_a_stock_delisted_between_preview_and_confirm_cannot_be_bought(db, mkt):  # D20
    trading.preview_order(mkt.user, order(mkt, "Buy", "Shares", "1"))
    delist(db, mkt.stock)
    with pytest.raises(SymbolNotFound):
        buy(mkt, "Shares", "1")
    unchanged(db, mkt)


# --- Preview, quote, and execution at the confirm-time price (R8.6, D11) --------------------------------

def test_preview_shows_quantity_and_total_and_changes_nothing(db, mkt):  # R8.6
    preview = trading.preview_order(mkt.user, order(mkt, "Buy", "Dollars", "100.00"))
    assert (preview.quantity, preview.total) == (D("3.333333"), D("100.00"))
    assert (preview.quote.symbol, preview.quote.price, preview.request.action) == ("ACME", D("30.00"), "Buy")
    unchanged(db, mkt)


def test_quote_shows_change_since_previous_close_and_shares_owned(db, mkt):  # R8.2, R9.1
    one(db, "INSERT INTO StockPrice (issuerID, price_date, open_price, close_price) VALUES (%s, %s, 24.00, 25.00)",
        (mkt.stock, date(2026, 9, 25)))  # Friday's close
    buy(mkt, "Shares", "1.5")
    q = trading.quote(mkt.user, "acme", mkt.portfolio)  # symbols are case-insensitive
    assert (q.symbol, q.company_name, q.price, q.previous_close) == ("ACME", "Acme Corp", D("30.00"), D("25.00"))
    assert (q.change, q.change_pct) == (D("5.00"), D("20.00"))
    assert q.market.is_open and q.shares_owned == D("1.5")
    assert trading.quote(mkt.user, "ACME").shares_owned is None  # no portfolio chosen


def test_execution_uses_the_price_at_confirmation(db, mkt):  # D11
    preview = trading.preview_order(mkt.user, order(mkt, "Buy", "Dollars", "100.00"))
    set_price(db, mkt.stock, D("40.00"))
    result = trading.execute_order(mkt.user, preview.request)
    assert result.price == D("40.00") and result.quantity == D("2.5")  # recalculated: never more than $100
    assert result.total == D("100.00")


def test_funds_are_checked_again_at_confirmation(db, mkt):  # R8.7, D11
    preview = trading.preview_order(mkt.user, order(mkt, "Buy", "Shares", "33"))  # 33 x 30 = 990.00: fits
    set_price(db, mkt.stock, D("31.00"))                                           # 33 x 31 = 1023.00: doesn't
    with pytest.raises(InsufficientFunds):
        trading.execute_order(mkt.user, preview.request)
    unchanged(db, mkt)


def test_a_failure_part_way_rolls_back_everything(db, mkt, monkeypatch):  # R8.10, R9.9, P9
    def fail(cur, **kwargs):
        cur.execute("INSERT INTO `Transaction` (portfolioID, issuerID, type, quantity, price_per_share, total_amount,"
                    " status, order_type, timing) VALUES (%s, %s, 'Buy', 1, 30.00, 30.00, 'Filled', 'Market', 'Day Only')",
                    (kwargs["portfolio_id"], kwargs["issuer_id"]))
        raise RuntimeError("database went away")

    monkeypatch.setattr(trading, "_record_trade", fail)
    with pytest.raises(RuntimeError):
        buy(mkt, "Shares", "1")
    unchanged(db, mkt)  # cash, position, supply, and the half-written Transaction are all rolled back


# --- Properties over random sequences: P1 (trades), P3, P7, P8, P18 --------------------------------------

STEPS = st.lists(
    st.tuples(
        st.sampled_from(["Buy", "Sell"]),
        st.sampled_from(["Dollars", "Shares"]),
        st.decimals(min_value=D("0.01"), max_value=D("400.00"), places=2),   # dollars, or shares / 10
        st.decimals(min_value=D("1.00"), max_value=D("250.00"), places=2),   # the price for this step
    ),
    min_size=1, max_size=10,
)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None, max_examples=40)
@given(steps=STEPS)
def test_properties_hold_over_any_sequence_of_trades(db, mkt, steps):
    with db.cursor() as cur:  # fresh books for every generated sequence
        cur.execute("DELETE FROM History")
        cur.execute("DELETE FROM `Transaction`")
        cur.execute("DELETE FROM Inventory")
        cur.execute("UPDATE Portfolio SET cash_balance = 5000.00 WHERE portfolioID = %s", (mkt.portfolio,))
        cur.execute("UPDATE Issuer SET shares_available = 500 WHERE issuerID = %s", (mkt.stock,))
    db.commit()
    expected_cash, held, average = D("5000.00"), D("0"), None
    for action, unit, raw, price in steps:
        set_price(db, mkt.stock, price)
        amount = raw if unit == "Dollars" else (raw / 10).quantize(D("0.000001"))
        try:
            result = trading.execute_order(mkt.user, order(mkt, action, unit, str(amount)))
        except (InsufficientFunds, InsufficientShares, NotEnoughSharesAvailable, ValidationFailed):
            continue
        if action == "Buy":
            if unit == "Dollars":
                assert result.total <= amount                                  # P7: never more than entered
                assert amount - result.total < price * D("0.000001") + D("0.01")  # ...and not much less
            average = result.price if held == 0 else \
                ((held * average + result.quantity * result.price) / (held + result.quantity)).quantize(D("0.01"))
            held += result.quantity
            expected_cash -= result.total
        else:
            held -= result.quantity
            expected_cash += result.total
        row = position(db, mkt.portfolio, mkt.stock)
        assert (row is not None) == (held > 0)                                 # P3: row iff quantity > 0
        if row:
            assert row["quantity"] == held                                     # P3
            assert abs(row["average_cost"] - average) <= D("0.01")             # P8
        else:
            average = None
        assert supply(db, mkt.stock) + held == D("500")                       # P18
    buys = one(db, "SELECT COALESCE(SUM(total_amount), 0) AS s FROM `Transaction` WHERE type = 'Buy'")["s"]
    sells = one(db, "SELECT COALESCE(SUM(total_amount), 0) AS s FROM `Transaction` WHERE type = 'Sell'")["s"]
    assert cash(db, mkt.portfolio) == expected_cash == D("5000.00") - buys + sells  # P1 for trades
    assert cash(db, mkt.portfolio) >= 0                                             # P2
    trades = one(db, "SELECT COUNT(*) AS n FROM `Transaction`")["n"]
    assert one(db, "SELECT COUNT(*) AS n FROM History WHERE transactionID IS NOT NULL")["n"] == trades  # P4


# --- Concurrency (N2.5) --------------------------------------------------------------------------------

def run_parallel(web_app, jobs):
    def run(job):
        with web_app.app_context():
            try:
                job()
                return "ok"
            except (InsufficientFunds, NotEnoughSharesAvailable) as exc:
                return type(exc).__name__

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return list(pool.map(run, jobs))  # anything else, such as a deadlock, raises here


def test_parallel_buys_never_overspend(db, web_app, mkt):  # N2.5, P2
    one(db, "UPDATE Portfolio SET cash_balance = 500.00 WHERE portfolioID = %s", (mkt.portfolio,))
    results = run_parallel(web_app, [lambda: buy(mkt, "Dollars", "100.00")] * 10)
    assert results.count("ok") == 5 and results.count("InsufficientFunds") == 5
    assert cash(db, mkt.portfolio) == D("0.00")
    assert position(db, mkt.portfolio, mkt.stock)["quantity"] == D("16.666665")  # 5 x 3.333333


def test_parallel_buys_by_different_customers_never_oversell(db, web_app, mkt):  # D16, P18, N2.5
    one(db, "UPDATE Issuer SET shares_available = 5 WHERE issuerID = %s", (mkt.stock,))
    buyers = []
    for _ in range(12):  # each opens a new position, the case that deadlocks under REPEATABLE READ gap locks
        user = make_user(db)
        buyers.append(type("Buyer", (), {"user": user, "portfolio": make_portfolio(db, user, cash_balance=D("100.00"))}))
    results = run_parallel(web_app, [lambda b=b: buy(b, "Shares", "1") for b in buyers])
    assert results.count("ok") == 5 and results.count("NotEnoughSharesAvailable") == 7
    assert supply(db, mkt.stock) == D("0")
    held = one(db, "SELECT SUM(quantity) AS s FROM Inventory WHERE issuerID = %s", (mkt.stock,))["s"]
    assert held == D("5")  # P18: nothing oversold

# --- Deadlock and lock-timeout retry (design: Error handling) --------------------------------------------

def flaky(failures, code):
    calls = []

    @retry_once_on_lock_conflict
    def work():
        calls.append(1)
        if len(calls) <= failures:
            raise pymysql.err.OperationalError(code, "simulated")
        return "done"
    return work, calls


@pytest.mark.parametrize("code", [1205, 1213])
def test_a_lock_conflict_is_retried_once(code):
    work, calls = flaky(1, code)
    assert work() == "done" and len(calls) == 2


def test_a_second_lock_conflict_is_not_retried_again():
    work, calls = flaky(2, 1213)
    with pytest.raises(pymysql.err.OperationalError):
        work()
    assert len(calls) == 2


def test_other_database_errors_are_not_retried():
    work, calls = flaky(1, 2013)  # lost connection
    with pytest.raises(pymysql.err.OperationalError):
        work()
    assert len(calls) == 1


def test_a_trade_chosen_as_deadlock_victim_succeeds_on_the_retry(db, mkt, monkeypatch):
    real = trading._record_trade
    attempts = []

    def deadlock_first_time(cur, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock")
        return real(cur, **kwargs)

    monkeypatch.setattr(trading, "_record_trade", deadlock_first_time)
    buy(mkt, "Shares", "2")
    assert len(attempts) == 2
    unchanged(db, mkt, cash_balance=D("940.00"), shares=D("998"), rows=(1, 1, 1))  # applied exactly once


# --- Limits are stated, not just "too large" ---------------------------------------------------------

@pytest.mark.parametrize("unit, amount, message", [
    ("Dollars", "100000000.00", "The largest amount you can enter is $99,999,999.99."),
    ("Shares", "1000000000000", "The largest number of shares you can enter is 999,999,999,999.999999."),
    ("Shares", "10000000", "An order total can't be more than $99,999,999.99."),
])
def test_order_limits_are_stated(db, mkt, unit, amount, message):
    with pytest.raises(ValidationFailed) as exc:
        buy(mkt, unit, amount)
    assert exc.value.message == message
