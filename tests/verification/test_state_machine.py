"""The P1-P9 state machine (task 5.2; design: Property-based testing conventions).

Hypothesis runs random sequences of deposits, withdrawals, moves between the customer's two portfolios,
buys and sells (by dollars or by shares), price ticks, and the market opening and closing, against the
real services and database. The machine keeps its own simple model of what every balance and holding
should be, computed independently from the Calculations table, and after every step checks:

- P1  each cash balance equals the model, and equals money in minus money out from the records alone;
- P2  no cash balance is negative;
- P3  each holding equals bought minus sold, and a row exists only while it is above zero;
- P4  every trade, deposit, and withdrawal has one History row, and every move has two;
- P5  a trade while the market is closed changes nothing (it raises MarketClosed);
- P8  average cost follows the weighted-average formula on each buy, and sells never change it;
- P9  a refused operation changes nothing (the model isn't updated, so any change is caught);
- P18 shares available plus holdings stays at the supply each stock started with.

The database is reset at the start of every example (not once per test), as the design requires.
With the default profile it runs 30 examples; HYPOTHESIS_PROFILE=thorough runs 1,000.
"""
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule, run_state_machine_as_test

from app import clock
from app.errors import (
    DomainError,
    InsufficientFunds,
    InsufficientShares,
    MarketClosed,
    NotEnoughSharesAvailable,
    ValidationFailed,
)
from app.migrate import APP_PRIVILEGES
from app.money import to_cents
from app.services import trading, transfers
from app.services.types import OrderRequest
from tests.factories import make_bank_account, make_portfolio, make_stock, make_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
OPEN = datetime(2026, 9, 29, 10, 0, tzinfo=AZ)      # Tuesday, trading hours
CLOSED = datetime(2026, 9, 29, 18, 0, tzinfo=AZ)    # same day, after the close
TODAY = date(2026, 9, 29)
SUPPLY = D("1000")
START_PRICES = {"XXX": D("33.33"), "YYY": D("120.10")}
SHARE = D("0.000001")

CENTS = st.decimals(min_value=D("0.01"), max_value=D("3000.00"), places=2)
SHARES = st.decimals(min_value=SHARE, max_value=D("40"), places=6)


def expected_size(unit: str, amount: Decimal, price: Decimal):
    """(quantity, total) per A3, or None if the order is too small; written independently of the app."""
    quantity = (amount / price).quantize(SHARE, rounding=ROUND_DOWN) if unit == "Dollars" else amount
    total = to_cents(quantity * price)
    if quantity <= 0 or total < D("0.01"):
        return None
    return quantity, total


def machine_for(db, web_app):
    class Money(RuleBasedStateMachine):
        def __init__(self):
            super().__init__()
            with db.cursor() as cur:
                cur.execute("SET FOREIGN_KEY_CHECKS = 0")
                for table in APP_PRIVILEGES:
                    cur.execute(f"TRUNCATE TABLE `{table}`")
                cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            db.commit()
            clock.freeze(OPEN)
            self.market_open = True
            self.user = make_user(db)
            self.portfolios = [make_portfolio(db, self.user, account_name=n) for n in ("A", "B")]
            self.bank = make_bank_account(db, self.user)
            self.stocks = {s: make_stock(db, symbol=s, price=p, shares_available=SUPPLY) for s, p in START_PRICES.items()}
            self.price = dict(START_PRICES)
            self.cash = {p: D("0.00") for p in self.portfolios}
            self.held = {}      # (portfolio, symbol) -> quantity
            self.average = {}   # (portfolio, symbol) -> average cost
            self.supply = {s: SUPPLY for s in self.stocks}
            self.history = {"trades": 0, "single": 0, "moves": 0}

        def expect(self, error, action):
            """Run `action`; it must raise `error` (or succeed when error is None). Returns the result."""
            if error is None:
                return action()
            try:
                action()
            except DomainError as raised:
                assert isinstance(raised, error), f"expected {error.__name__}, got {type(raised).__name__}"
                return None
            raise AssertionError(f"expected {error.__name__}, but it succeeded")

        # --- rules ---------------------------------------------------------------------------------------

        @initialize(amounts=st.tuples(CENTS, CENTS))
        def opening_deposits(self, amounts):
            """Start with money in both portfolios, so most sequences reach buys and sells."""
            for portfolio, amount in zip(self.portfolios, amounts):
                transfers.deposit(self.user, self.bank, portfolio, amount + D("2000"), TODAY, at_utc=clock.now_utc())
                self.cash[portfolio] += amount + D("2000")
                self.history["single"] += 1

        @rule(which=st.integers(0, 1), amount=CENTS)
        def deposit(self, which, amount):
            portfolio = self.portfolios[which]
            self.expect(None, lambda: transfers.deposit(self.user, self.bank, portfolio, amount, TODAY, at_utc=clock.now_utc()))
            self.cash[portfolio] += amount
            self.history["single"] += 1

        @rule(which=st.integers(0, 1), amount=CENTS)
        def withdraw(self, which, amount):
            portfolio = self.portfolios[which]
            refused = amount > self.cash[portfolio]
            self.expect(InsufficientFunds if refused else None,
                        lambda: transfers.withdraw(self.user, portfolio, self.bank, amount, TODAY, at_utc=clock.now_utc()))
            if not refused:
                self.cash[portfolio] -= amount
                self.history["single"] += 1

        @rule(forward=st.booleans(), amount=CENTS)
        def move(self, forward, amount):
            source, target = self.portfolios if forward else reversed(self.portfolios)
            refused = amount > self.cash[source]
            self.expect(InsufficientFunds if refused else None,
                        lambda: transfers.move_between(self.user, source, target, amount, TODAY, at_utc=clock.now_utc()))
            if not refused:
                self.cash[source] -= amount
                self.cash[target] += amount
                self.history["moves"] += 1

        @rule(which=st.integers(0, 1), symbol=st.sampled_from(sorted(START_PRICES)), unit=st.sampled_from(["Dollars", "Shares"]),
              dollars=CENTS, shares=SHARES)
        def buy(self, which, symbol, unit, dollars, shares):
            portfolio, amount = self.portfolios[which], dollars if unit == "Dollars" else shares
            key, price = (portfolio, symbol), self.price[symbol]
            size = expected_size(unit, amount, price)
            if not self.market_open:
                error = MarketClosed
            elif size is None:
                error = ValidationFailed
            elif size[0] > self.supply[symbol]:
                error = NotEnoughSharesAvailable
            elif size[1] > self.cash[portfolio]:
                error = InsufficientFunds
            else:
                error = None
            result = self.expect(error, lambda: trading.execute_order(
                self.user, OrderRequest(portfolio, symbol, "Buy", unit, amount), at_utc=clock.now_utc()))
            if error is None:
                quantity, total = size
                assert (result.quantity, result.total, result.price) == (quantity, total, price)
                held = self.held.get(key, D("0"))
                self.average[key] = to_cents((held * self.average[key] + quantity * price) / (held + quantity)) if held else price
                self.held[key] = held + quantity
                self.cash[portfolio] -= total
                self.supply[symbol] -= quantity
                self.history["trades"] += 1

        @rule(which=st.integers(0, 1), symbol=st.sampled_from(sorted(START_PRICES)), unit=st.sampled_from(["Dollars", "Shares", "All"]),
              dollars=CENTS, shares=SHARES)
        def sell(self, which, symbol, unit, dollars, shares):
            portfolio = self.portfolios[which]
            key, price = (portfolio, symbol), self.price[symbol]
            held = self.held.get(key, D("0"))
            if unit == "All":
                unit, amount = "Shares", held if held > 0 else shares   # sell the whole position
            elif unit == "Shares" and held > 0 and shares < 1:
                amount = max(SHARE, (held * shares).quantize(SHARE, rounding=ROUND_DOWN))  # part of what is held
            else:
                amount = dollars if unit == "Dollars" else shares
            size = expected_size(unit, amount, price)
            if not self.market_open:
                error = MarketClosed
            elif size is None:
                error = ValidationFailed
            elif size[0] > held:
                error = InsufficientShares
            else:
                error = None
            result = self.expect(error, lambda: trading.execute_order(
                self.user, OrderRequest(portfolio, symbol, "Sell", unit, amount), at_utc=clock.now_utc()))
            if error is None:
                quantity, total = size
                assert (result.quantity, result.total) == (quantity, total)
                self.held[key] = held - quantity
                if self.held[key] == 0:
                    del self.held[key], self.average[key]
                self.cash[portfolio] += total
                self.supply[symbol] += quantity
                self.history["trades"] += 1

        @rule(symbol=st.sampled_from(sorted(START_PRICES)), change=st.decimals(min_value=D("-0.2"), max_value=D("0.2"), places=3))
        def tick(self, symbol, change):
            new_price = max(D("0.01"), to_cents(self.price[symbol] * (1 + change)))
            with db.cursor() as cur:
                cur.execute("UPDATE Issuer SET current_price = %s WHERE issuerID = %s", (new_price, self.stocks[symbol]))
            db.commit()
            self.price[symbol] = new_price

        @rule(open_=st.booleans())
        def market(self, open_):
            clock.freeze(OPEN if open_ else CLOSED)
            self.market_open = open_

        # --- invariants ----------------------------------------------------------------------------------

        def query(self, sql, params=()):
            with db.cursor() as cur:
                cur.execute(sql, params)
                found = cur.fetchall()
            db.commit()
            return list(found)

        @invariant()
        def p1_p2_cash(self):
            for portfolio in self.portfolios:
                stored = self.query("SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (portfolio,))[0]["cash_balance"]
                assert stored == self.cash[portfolio]                                       # P1 (model)
                assert stored >= 0                                                         # P2
                money = self.query(
                    "SELECT"
                    " (SELECT COALESCE(SUM(amount), 0) FROM Transfer WHERE status = 'Completed' AND to_portfolioID = %s)"
                    " - (SELECT COALESCE(SUM(amount), 0) FROM Transfer WHERE status = 'Completed' AND from_portfolioID = %s)"
                    " + (SELECT COALESCE(SUM(total_amount), 0) FROM `Transaction` WHERE type = 'Sell' AND portfolioID = %s)"
                    " - (SELECT COALESCE(SUM(total_amount), 0) FROM `Transaction` WHERE type = 'Buy' AND portfolioID = %s) AS net",
                    (portfolio, portfolio, portfolio, portfolio))[0]["net"]
                assert money == stored                                                    # P1 (records, R17.3)

        @invariant()
        def p3_p8_holdings(self):
            rows = self.query("SELECT inv.portfolioID, i.ticker_symbol, inv.quantity, inv.average_cost FROM Inventory inv"
                              " JOIN Issuer i ON i.issuerID = inv.issuerID")
            stored = {(r["portfolioID"], r["ticker_symbol"]): (r["quantity"], r["average_cost"]) for r in rows}
            assert stored == {key: (self.held[key], self.average[key]) for key in self.held}     # P3, P8
            assert all(quantity > 0 for quantity, _ in stored.values())                           # P3: no zero rows
            traded = self.query("SELECT t.portfolioID, i.ticker_symbol,"
                                " SUM(CASE WHEN t.type = 'Buy' THEN t.quantity ELSE -t.quantity END) AS net"
                                " FROM `Transaction` t JOIN Issuer i ON i.issuerID = t.issuerID GROUP BY t.portfolioID, i.ticker_symbol")
            assert {(r["portfolioID"], r["ticker_symbol"]): r["net"] for r in traded if r["net"] != 0} == \
                {key: quantity for key, (quantity, _) in stored.items()}                          # P3 from the records

        @invariant()
        def p4_history(self):
            counts = self.query(
                "SELECT (SELECT COUNT(*) FROM History WHERE transactionID IS NOT NULL) AS trade_rows,"
                " (SELECT COUNT(*) FROM `Transaction`) AS trades,"
                " (SELECT COUNT(*) FROM History h JOIN Transfer t ON t.transferID = h.transferID"
                "  WHERE t.transfer_type IN ('Deposit', 'Withdrawal')) AS single_rows,"
                " (SELECT COUNT(*) FROM Transfer WHERE transfer_type IN ('Deposit', 'Withdrawal')) AS singles,"
                " (SELECT COUNT(*) FROM History h JOIN Transfer t ON t.transferID = h.transferID"
                "  WHERE t.transfer_type = 'Internal') AS move_rows,"
                " (SELECT COUNT(*) FROM Transfer WHERE transfer_type = 'Internal') AS moves")[0]
            assert counts["trade_rows"] == counts["trades"] == self.history["trades"]
            assert counts["single_rows"] == counts["singles"] == self.history["single"]
            assert counts["move_rows"] == 2 * counts["moves"] == 2 * self.history["moves"]

        @invariant()
        def p18_supply(self):
            for symbol, issuer in self.stocks.items():
                available = self.query("SELECT shares_available FROM Issuer WHERE issuerID = %s", (issuer,))[0]["shares_available"]
                holdings = sum((q for (p, s), q in self.held.items() if s == symbol), D("0"))
                assert available == self.supply[symbol] and available + holdings == SUPPLY

    return Money


def test_p1_to_p9_hold_over_any_sequence(db, web_app):  # P1-P5, P8, P9, P18
    thorough = settings.default.max_examples >= 1000
    run_settings = settings(max_examples=1000 if thorough else 30, stateful_step_count=25, deadline=None,
                            suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
    try:
        with web_app.app_context():
            run_state_machine_as_test(machine_for(db, web_app), settings=run_settings)
    finally:
        clock.unfreeze()
