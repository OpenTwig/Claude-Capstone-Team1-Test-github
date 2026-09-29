"""Rounding edges (task 5.2; A3, P7, P8, R8.5, the Calculations table).

Money rounds half-up to the cent; a Dollars order's quantity rounds down to 6 places, so it never
spends more than the amount entered.
"""
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.errors import InsufficientFunds, ValidationFailed
from app.money import MAX_MONEY, to_cents
from app.services import trading, transfers
from app.services.trading import size_order
from app.services.types import OrderRequest
from tests.factories import make_bank_account, make_portfolio, make_stock, make_user

D = Decimal
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("America/Phoenix"))
TODAY = date(2026, 9, 29)
SHARE = D("0.000001")


def order(unit, amount, action="Buy", portfolio=0, symbol="XXX"):
    return OrderRequest(portfolio, symbol, action, unit, D(amount))


# --- Amounts that don't divide evenly (pure: no database) ---------------------------------------------

@pytest.mark.parametrize("amount, price, quantity, total", [
    ("100.00", "33.33", "3.000300", "100.00"),    # 3.0003 x 33.33 = 99.999999 -> 100.00
    ("10.00", "3.00", "3.333333", "10.00"),       # 9.999999 -> 10.00
    ("1.00", "0.07", "14.285714", "1.00"),        # 0.99999998 -> 1.00
    ("0.05", "0.03", "1.666666", "0.05"),         # 0.04999998 -> 0.05
    ("250.00", "33.33", "7.500750", "250.00"),    # 249.9999975 -> 250.00
    ("0.01", "5.66", "0.001766", "0.01"),         # a $0.01 purchase: 0.00999556 -> 0.01
    ("99999999.99", "1000.00", "99999.999990", "99999999.99"),  # the largest amount
])
def test_dollars_orders_that_do_not_divide_evenly(amount, price, quantity, total):  # A3, P7
    assert size_order(order("Dollars", amount), D(price)) == (D(quantity), D(total))


@pytest.mark.parametrize("quantity, price, total", [
    ("0.000001", "10000.00", "0.01"),   # the smallest quantity of an expensive stock
    ("0.000001", "5000.00", "0.01"),    # 0.005 rounds half-up to 0.01
    ("1.234567", "81.00", "100.00"),    # 99.999927 -> 100.00
    ("2.5", "120.10", "300.25"),
])
def test_shares_orders_round_half_up(quantity, price, total):  # A3
    assert size_order(order("Shares", quantity), D(price)) == (D(quantity), D(total))


@pytest.mark.parametrize("unit, amount, price", [
    ("Shares", "0.000001", "4999.99"),   # 0.00499999 -> $0.00: a trade must cost at least a cent
    ("Dollars", "0.01", "10000.01"),     # less than 0.000001 of a share
])
def test_orders_too_small_to_cost_a_cent_are_refused(unit, amount, price):  # A3
    with pytest.raises(ValidationFailed):
        size_order(order(unit, amount), D(price))


@given(amount=st.decimals(min_value=D("0.01"), max_value=D("100000.00"), places=2),
       price=st.decimals(min_value=D("0.01"), max_value=D("10000.00"), places=2))
def test_p7_a_dollars_order_never_spends_more_than_entered(amount, price):  # P7
    try:
        quantity, total = size_order(order("Dollars", amount), price)
    except ValidationFailed:
        assert amount / price < SHARE or to_cents((amount / price).quantize(SHARE, ROUND_DOWN) * price) < D("0.01")
        return
    assert total <= amount                                      # never more than entered
    assert amount - quantity * price < SHARE * price            # less than one share-increment left unspent
    assert quantity == quantity.quantize(SHARE) and total == to_cents(total)


# --- Through the service, with the database -------------------------------------------------------------

@pytest.fixture
def account(db, app_ctx, fixed_clock):
    fixed_clock(NOW)
    user = make_user(db)
    return type("A", (), {"user": user, "portfolio": make_portfolio(db, user, cash_balance=D("0.00")),
                          "bank": make_bank_account(db, user)})


def cash(db, portfolio):
    with db.cursor() as cur:
        cur.execute("SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (portfolio,))
        value = cur.fetchone()["cash_balance"]
    db.commit()
    return value


def avg_cost(db, portfolio, issuer):
    with db.cursor() as cur:
        cur.execute("SELECT quantity, average_cost FROM Inventory WHERE portfolioID = %s AND issuerID = %s", (portfolio, issuer))
        row = cur.fetchone()
    db.commit()
    return (row["quantity"], row["average_cost"]) if row else None


@pytest.mark.db
def test_the_largest_amount_end_to_end(db, account):  # the column maximum, 99,999,999.99
    transfers.deposit(account.user, account.bank, account.portfolio, MAX_MONEY, TODAY, at_utc=NOW)
    assert cash(db, account.portfolio) == MAX_MONEY
    with pytest.raises(ValidationFailed):
        transfers.deposit(account.user, account.bank, account.portfolio, D("0.01"), TODAY, at_utc=NOW)
    issuer = make_stock(db, symbol="XXX", price=D("1000.00"), shares_available=D("1000000"))
    result = trading.execute_order(account.user, order("Dollars", "99999999.99", portfolio=account.portfolio), at_utc=NOW)
    assert (result.quantity, result.total, result.cash_balance) == (D("99999.999990"), MAX_MONEY, D("0.00"))
    assert avg_cost(db, account.portfolio, issuer) == (D("99999.999990"), D("1000.00"))


@pytest.mark.db
def test_a_one_cent_purchase(db, account):
    transfers.deposit(account.user, account.bank, account.portfolio, D("0.01"), TODAY, at_utc=NOW)
    issuer = make_stock(db, symbol="XXX", price=D("5.66"))
    result = trading.execute_order(account.user, order("Dollars", "0.01", portfolio=account.portfolio), at_utc=NOW)
    assert (result.quantity, result.total, cash(db, account.portfolio)) == (D("0.001766"), D("0.01"), D("0.00"))
    with pytest.raises(InsufficientFunds):
        trading.execute_order(account.user, order("Dollars", "0.01", portfolio=account.portfolio), at_utc=NOW)
    assert avg_cost(db, account.portfolio, issuer) == (D("0.001766"), D("5.66"))


@pytest.mark.db
def test_average_cost_after_mixed_buys_and_sells(db, account):  # P8, R9.6, Calculations table
    transfers.deposit(account.user, account.bank, account.portfolio, D("1000.00"), TODAY, at_utc=NOW)
    issuer = make_stock(db, symbol="XXX", price=D("10.00"))

    def trade(action, shares, price):
        with db.cursor() as cur:
            cur.execute("UPDATE Issuer SET current_price = %s WHERE issuerID = %s", (D(price), issuer))
        db.commit()
        trading.execute_order(account.user, order("Shares", shares, action, account.portfolio), at_utc=NOW)
        return avg_cost(db, account.portfolio, issuer)

    assert trade("Buy", "1", "10.00") == (D("1"), D("10.00"))
    assert trade("Buy", "3", "14.00") == (D("4"), D("13.00"))           # (1x10 + 3x14) / 4 = 13.00
    assert trade("Sell", "2", "20.00") == (D("2"), D("13.00"))          # sells never change it
    assert trade("Buy", "1", "20.00") == (D("3"), D("15.33"))           # (2x13 + 1x20) / 3 = 15.333.. -> 15.33
    assert trade("Buy", "2", "10.01") == (D("5"), D("13.20"))           # (3x15.33 + 2x10.01) / 5 = 13.202 -> 13.20
    assert trade("Sell", "5", "9.00") is None                           # sold out: the row is gone (P3)
    assert trade("Buy", "0.5", "7.00") == (D("0.5"), D("7.00"))         # starts fresh
    # 1,000 - 10 - 42 + 40 - 20 - 20.02 + 45 - 3.50 = 989.48
    assert cash(db, account.portfolio) == D("989.48")
