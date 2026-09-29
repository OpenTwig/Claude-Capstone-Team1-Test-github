"""Concurrency and integrity (task 5.5; N2.5, N6, R8.10, R9.9, R17, D16).

Requests race each other on separate connections, released together by a barrier. After every run the
books must still balance: P1 (cash = money in - money out, from the records), P2 (no negative cash),
P3 (holdings = bought - sold, no zero rows), P4 (History rows), and P18 (share supply conserved).
The audit tables can't be changed or deleted by the application's database account (R17.2).
"""
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pymysql
import pytest

from app import clock
from app.db import connect
from app.errors import InsufficientFunds, InsufficientShares, NotEnoughSharesAvailable
from app.money import to_cents
from app.services import price_generator, trading, transfers
from app.services.pricing import PriceModel
from app.services.types import OrderRequest
from tests.factories import make_bank_account, make_portfolio, make_stock, make_user

pytestmark = pytest.mark.db

D = Decimal
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("America/Phoenix"))
TODAY = date(2026, 9, 29)
SUPPLY = D("1000")
EXPECTED_REFUSALS = (InsufficientFunds, InsufficientShares, NotEnoughSharesAvailable)


def rows(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchall()
    db.commit()
    return list(found)


def one(db, sql, params=()):
    found = rows(db, sql, params)
    return found[0] if found else None


@pytest.fixture
def market(db, web_app, fixed_clock):
    fixed_clock(NOW)
    user = make_user(db)
    return type("M", (), {
        "user": user,
        "portfolio": make_portfolio(db, user, cash_balance=D("0.00")),
        "other": make_portfolio(db, user, account_name="Other", cash_balance=D("0.00")),
        "bank": make_bank_account(db, user),
        "stock": make_stock(db, symbol="XXX", price=D("10.00"), shares_available=SUPPLY),
        "app": web_app,
    })


def setup_money(m, cash="0.00", shares=None):
    """Put cash (and optionally a holding) in place through the real services, so the records add up (P1)."""
    with m.app.app_context():
        if D(cash) > 0:
            transfers.deposit(m.user, m.bank, m.portfolio, D(cash), TODAY, at_utc=NOW)
        if shares:
            transfers.deposit(m.user, m.bank, m.portfolio, D(shares) * D("10.00"), TODAY, at_utc=NOW)
            trading.execute_order(m.user, OrderRequest(m.portfolio, "XXX", "Buy", "Shares", D(shares)), at_utc=NOW)


def race(app, jobs):
    """Run the jobs at the same moment, each on its own connection. Expected refusals are returned by name;
    anything else (a deadlock that wasn't retried, a server error) makes the test fail."""
    barrier = threading.Barrier(len(jobs))

    def run(job):
        with app.app_context():
            barrier.wait()
            try:
                job()
                return "ok"
            except EXPECTED_REFUSALS as exc:
                return type(exc).__name__

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return list(pool.map(run, jobs))


def buy(m, unit, amount):
    return lambda: trading.execute_order(m.user, OrderRequest(m.portfolio, "XXX", "Buy", unit, D(amount)), at_utc=NOW)


def sell(m, unit, amount):
    return lambda: trading.execute_order(m.user, OrderRequest(m.portfolio, "XXX", "Sell", unit, D(amount)), at_utc=NOW)


def assert_books_balance(db):
    """P1-P4 and P18, from the database alone."""
    for p in rows(db, "SELECT portfolioID, cash_balance FROM Portfolio"):
        pid = p["portfolioID"]
        net = one(db, "SELECT"
                      " (SELECT COALESCE(SUM(amount), 0) FROM Transfer WHERE status = 'Completed' AND to_portfolioID = %s)"
                      " - (SELECT COALESCE(SUM(amount), 0) FROM Transfer WHERE status = 'Completed' AND from_portfolioID = %s)"
                      " + (SELECT COALESCE(SUM(total_amount), 0) FROM `Transaction` WHERE type = 'Sell' AND portfolioID = %s)"
                      " - (SELECT COALESCE(SUM(total_amount), 0) FROM `Transaction` WHERE type = 'Buy' AND portfolioID = %s) AS net",
                  (pid, pid, pid, pid))["net"]
        assert p["cash_balance"] == net, f"P1: portfolio {pid} holds {p['cash_balance']} but its records add up to {net}"
        assert p["cash_balance"] >= 0, "P2: negative cash"
    held = {(r["portfolioID"], r["issuerID"]): r["quantity"] for r in rows(db, "SELECT portfolioID, issuerID, quantity FROM Inventory")}
    traded = {(r["portfolioID"], r["issuerID"]): r["net"] for r in rows(
        db, "SELECT portfolioID, issuerID, SUM(CASE WHEN type = 'Buy' THEN quantity ELSE -quantity END) AS net"
            " FROM `Transaction` GROUP BY portfolioID, issuerID") if r["net"] != 0}
    assert held == traded, "P3: holdings don't match the trades"
    assert all(q > 0 for q in held.values()), "P3: a zero-quantity row"
    counts = one(db, "SELECT (SELECT COUNT(*) FROM `Transaction`) AS trades,"
                     " (SELECT COUNT(*) FROM History WHERE transactionID IS NOT NULL) AS trade_rows,"
                     " (SELECT COUNT(*) + SUM(transfer_type = 'Internal') FROM Transfer) AS transfer_rows_expected,"
                     " (SELECT COUNT(*) FROM History WHERE transferID IS NOT NULL) AS transfer_rows")
    assert counts["trades"] == counts["trade_rows"], "P4: a trade without exactly one History row"
    assert (counts["transfer_rows_expected"] or 0) == counts["transfer_rows"], "P4: transfer History rows"
    for s in rows(db, "SELECT issuerID, shares_available FROM Issuer"):
        holdings = sum((q for (p, i), q in held.items() if i == s["issuerID"]), D("0"))
        assert s["shares_available"] + holdings == SUPPLY, "P18: shares created or lost"


# --- Races --------------------------------------------------------------------------------------------------

def test_parallel_withdrawals_never_overdraw(db, market):  # R12.3, N2.5, P2
    setup_money(market, cash="500.00")
    withdraw = lambda: transfers.withdraw(market.user, market.portfolio, market.bank, D("100.00"), TODAY, at_utc=NOW)  # noqa: E731
    results = race(market.app, [withdraw] * 10)
    assert results.count("ok") == 5 and results.count("InsufficientFunds") == 5
    assert one(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (market.portfolio,))["cash_balance"] == D("0.00")
    assert_books_balance(db)


def test_buys_and_sells_on_the_same_position_at_once(db, market):  # R8.7, R9.6, N2.5
    setup_money(market, cash="1000.00", shares="10")
    results = race(market.app, [buy(market, "Shares", "1")] * 8 + [sell(market, "Shares", "1")] * 8)
    assert results == ["ok"] * 16          # 10 held to start, so every sell has a share to sell
    position = one(db, "SELECT quantity, average_cost FROM Inventory WHERE portfolioID = %s", (market.portfolio,))
    assert (position["quantity"], position["average_cost"]) == (D("10"), D("10.00"))
    assert one(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (market.portfolio,))["cash_balance"] == D("1000.00")
    assert_books_balance(db)


def test_sells_racing_for_the_last_shares(db, market):  # R9.4, P3
    setup_money(market, shares="5")
    results = race(market.app, [sell(market, "Shares", "2")] * 6)
    assert results.count("ok") == 2 and results.count("InsufficientShares") == 4   # 2 + 2, then 1 left
    assert one(db, "SELECT quantity FROM Inventory WHERE portfolioID = %s", (market.portfolio,))["quantity"] == D("1")
    assert_books_balance(db)


def test_deposits_and_moves_during_trading(db, market):  # R11, R19, N2.5
    setup_money(market, cash="200.00")
    deposit = lambda: transfers.deposit(market.user, market.bank, market.portfolio, D("100.00"), TODAY, at_utc=NOW)  # noqa: E731
    move = lambda: transfers.move_between(market.user, market.portfolio, market.other, D("30.00"), TODAY, at_utc=NOW)  # noqa: E731
    jobs = [deposit] * 6 + [buy(market, "Dollars", "50.00")] * 6 + [move] * 4
    random.Random(55).shuffle(jobs)
    results = race(market.app, jobs)
    assert set(results) <= {"ok", "InsufficientFunds"}
    assert_books_balance(db)   # whatever the order, every accepted request is fully recorded and nothing else


def test_price_ticks_during_trades(db, market):  # D16, R16, N2.5
    """The generator waits for a trade's Issuer lock; every trade fills at the price it saw, consistently."""
    setup_money(market, cash="5000.00")
    settings = market.app.config["SETTINGS"]
    state, rng = price_generator.TickState(), random.Random(7)

    def ticks():
        for _ in range(15):
            price_generator.tick(state, clock.now_utc(), rng, PriceModel.from_settings(settings))

    results = race(market.app, [ticks] + [buy(market, "Dollars", "40.00")] * 10 + [sell(market, "Dollars", "5.00")] * 5)
    assert results[0] == "ok"
    for t in rows(db, "SELECT type, quantity, price_per_share, total_amount FROM `Transaction`"):
        assert t["total_amount"] == to_cents(t["quantity"] * t["price_per_share"])     # priced consistently
        if t["type"] == "Buy":
            assert t["total_amount"] <= D("40.00")                                     # P7 held at a moving price
    stock = one(db, "SELECT current_price FROM Issuer WHERE issuerID = %s", (market.stock,))
    today = one(db, "SELECT close_price FROM StockPrice WHERE issuerID = %s AND price_date = %s", (market.stock, TODAY))
    assert today["close_price"] == stock["current_price"]                               # the tick's writes agree
    assert_books_balance(db)


def test_a_trade_waits_for_a_lock_held_by_another_connection(db, market, test_database):  # N2.5
    """While another connection holds the stock's row lock, a buy waits for it, then completes once."""
    setup_money(market, cash="100.00")
    holder = connect(test_database.settings)
    finished = []
    try:
        with holder.cursor() as cur:
            holder.begin()
            cur.execute("SELECT current_price FROM Issuer WHERE issuerID = %s FOR UPDATE", (market.stock,))

            def buy_in_background():
                with market.app.app_context():
                    buy(market, "Dollars", "20.00")()
                finished.append(datetime.now(timezone.utc))

            worker = threading.Thread(target=buy_in_background)
            worker.start()
            worker.join(timeout=1.0)
            assert worker.is_alive() and not finished, "the buy should be waiting for the lock"
            cur.execute("UPDATE Issuer SET current_price = 20.00 WHERE issuerID = %s", (market.stock,))
            holder.commit()                         # release: the buy now sees the new price
        worker.join(timeout=10)
        assert finished, "the buy never finished"
    finally:
        holder.close()
    trade = one(db, "SELECT quantity, price_per_share, total_amount FROM `Transaction`")
    assert (trade["price_per_share"], trade["quantity"], trade["total_amount"]) == (D("20.00"), D("1"), D("20.00"))
    assert_books_balance(db)


# --- The audit trail can't be edited (R17.2) ------------------------------------------------------------------

@pytest.mark.parametrize("statement", [
    "UPDATE History SET action = 'Tampered'",
    "DELETE FROM History",
    "UPDATE `Transaction` SET total_amount = 0.01",
    "DELETE FROM `Transaction`",
    "DELETE FROM Transfer",
    "TRUNCATE TABLE History",
])
def test_the_app_account_cannot_edit_the_audit_trail(db, market, app_db, statement):  # R17.2, N3.6
    setup_money(market, shares="2")
    with app_db.cursor() as cur:
        with pytest.raises(pymysql.err.OperationalError) as exc:
            cur.execute(statement)
    assert exc.value.args[0] in (1142, 1143), exc.value   # "command denied to user"
    app_db.rollback()
    assert one(db, "SELECT COUNT(*) AS n FROM History")["n"] == 2   # the setup's deposit and buy, untouched


def test_no_application_code_edits_the_audit_trail():  # R17.2
    """Scanned from the source: nothing in app/ updates or deletes History or Transaction rows, or deletes transfers."""
    forbidden = ("UPDATE History", "DELETE FROM History", "UPDATE `Transaction`", "DELETE FROM `Transaction`",
                 "DELETE FROM Transfer", "TRUNCATE")
    app_dir = Path(__file__).resolve().parents[2] / "app"
    found = [f"{path.name}: {text}" for path in app_dir.rglob("*.py")
             for text in forbidden if text.lower() in path.read_text(encoding="utf-8").lower()]
    assert not found, found
