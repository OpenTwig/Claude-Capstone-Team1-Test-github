"""Tampered forms (task 5.2; R8, R9, R11, R12, R2.5).

Each form is sent twice, to two identical customers: once as the page sends it, once with altered or
extra fields (a price, total, quantity, or cash balance of the sender's choosing, or another customer's
IDs). The server computes everything itself, so the tampered request must end exactly like the clean
one, or be refused and change nothing.
"""
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.crypto import encrypt_field
from app.migrate import APP_PRIVILEGES
from tests.factories import make_bank_account, make_portfolio, make_stock
from tests.web import csrf_token, login, make_login_user

pytestmark = pytest.mark.db

D = Decimal
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("America/Phoenix"))
TODAY = date(2026, 9, 29)
PASSWORD = "correct horse battery"

TAMPER = {"price": "0.01", "price_per_share": "0.01", "total": "0.01", "total_amount": "0.01", "quantity": "999999",
          "cash_balance": "99999999.99", "average_cost": "0.01", "status": "Pending", "user_id": "1", "userID": "1"}


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def snapshot(db, user):
    """Everything a request could have changed for this customer, without IDs or timestamps."""
    with db.cursor() as cur:
        cur.execute("SELECT portfolioID, cash_balance FROM Portfolio WHERE userID = %s ORDER BY portfolioID", (user,))
        portfolios = cur.fetchall()
        ids = [p["portfolioID"] for p in portfolios] or [0]
        marks = ", ".join(["%s"] * len(ids))
        cur.execute(f"SELECT issuerID, quantity, average_cost FROM Inventory WHERE portfolioID IN ({marks}) ORDER BY issuerID", ids)
        inventory = cur.fetchall()
        cur.execute(f"SELECT type, quantity, price_per_share, total_amount, status FROM `Transaction`"
                    f" WHERE portfolioID IN ({marks}) ORDER BY transactionID", ids)
        trades = cur.fetchall()
        cur.execute(f"SELECT transfer_type, amount, status FROM Transfer WHERE from_portfolioID IN ({marks})"
                    f" OR to_portfolioID IN ({marks}) ORDER BY transferID", ids + ids)
        transfers = cur.fetchall()
        cur.execute("SELECT shares_available FROM Issuer ORDER BY issuerID")
        supply = cur.fetchall()
    db.commit()
    return ([p["cash_balance"] for p in portfolios], [tuple(r.values()) for r in inventory],
            [tuple(r.values()) for r in trades], [tuple(r.values()) for r in transfers], [r["shares_available"] for r in supply])


def counts(db):
    return {t: one(db, f"SELECT COUNT(*) AS n FROM `{t}`")["n"] for t in APP_PRIVILEGES}


@pytest.fixture
def pair(db, web_app, fixed_clock):
    """Two customers with the same starting money, and a stock each can trade."""
    fixed_clock(NOW)
    make_stock(db, symbol="XXX", price=D("33.33"), shares_available=D("1000"))
    people = []
    for n in (1, 2):
        user = make_login_user(db, PASSWORD, email=f"c{n}@example.test")
        main = make_portfolio(db, user, account_name="Main", cash_balance=D("1000.00"))
        second = make_portfolio(db, user, account_name="Second", cash_balance=D("0.00"))
        with web_app.app_context():
            bank = make_bank_account(db, user, account_number=encrypt_field("000123456789"))
        people.append(type("P", (), {"user": user, "email": f"c{n}@example.test", "main": main, "second": second, "bank": bank}))
    return people


def as_customer(web_app, person):
    client = web_app.test_client()
    login(client, person.email, PASSWORD)
    return client


def buy(client, person, extra=None, confirm_extra=None, unit="Dollars", amount="250.00", action="Buy"):
    data = {"portfolio": person.main, "symbol": "XXX", "action": action, "unit": unit, "amount": amount,
            "order_type": "Market", "timing": "Day Only", "csrf_token": csrf_token(client, "/trade")}
    data.update(extra or {})
    preview = client.post("/trade/preview", data=data)
    found = re.search(r'name="nonce" value="([^"]+)"', preview.get_data(as_text=True))
    if found is None:
        return preview
    return client.post("/trade/confirm", data={"nonce": found.group(1), "csrf_token": csrf_token(client, "/trade"),
                                              **(confirm_extra or {})})


def test_tampered_preview_and_confirm_end_like_the_clean_order(db, web_app, pair):  # R8, R9
    clean, tampered = pair
    buy(as_customer(web_app, clean), clean)
    buy(as_customer(web_app, tampered), tampered, extra=TAMPER, confirm_extra={**TAMPER, "amount": "999", "symbol": "ZZZ",
                                                                                "action": "Sell", "unit": "Shares"})
    assert snapshot(db, clean.user) == snapshot(db, tampered.user)
    assert one(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (tampered.main,))["cash_balance"] == D("750.00")


def test_tampered_sell_ends_like_the_clean_sell(db, web_app, pair):  # R9
    clean, tampered = pair
    for person in pair:
        buy(as_customer(web_app, person), person)
    buy(as_customer(web_app, clean), clean, action="Sell", unit="Shares", amount="2")
    buy(as_customer(web_app, tampered), tampered, action="Sell", unit="Shares", amount="2", extra=TAMPER, confirm_extra=TAMPER)
    assert snapshot(db, clean.user) == snapshot(db, tampered.user)


def test_another_customers_portfolio_in_the_order_is_refused(db, web_app, pair):  # R2.5
    me, other = pair
    before = counts(db), snapshot(db, other.user)
    response = buy(as_customer(web_app, me), me, extra={"portfolio": other.main})
    assert response.status_code == 404
    assert (counts(db), snapshot(db, other.user)) == before


def transfer(client, source, target, amount="100.00", extra=None):
    data = {"from_account": source, "to_account": target, "amount": amount, "transfer_date": TODAY.isoformat(),
            "csrf_token": csrf_token(client, "/transfer")}
    data.update(extra or {})
    return client.post("/transfer", data=data)


@pytest.mark.parametrize("kind", ["deposit", "withdrawal", "move"])
def test_tampered_transfers_end_like_the_clean_ones(db, web_app, pair, kind):  # R11, R12, R19
    results = []
    for person, extra in zip(pair, (None, {**TAMPER, "to_cash_balance": "99999999.99", "portfolio_id": "1"})):
        source, target = {"deposit": (f"bank:{person.bank}", f"portfolio:{person.main}"),
                          "withdrawal": (f"portfolio:{person.main}", f"bank:{person.bank}"),
                          "move": (f"portfolio:{person.main}", f"portfolio:{person.second}")}[kind]
        assert transfer(as_customer(web_app, person), source, target, extra=extra).status_code == 302
        results.append(snapshot(db, person.user))
    assert results[0] == results[1]


@pytest.mark.parametrize("field, pick", [
    ("from_account", lambda me, other: (f"portfolio:{other.main}", f"bank:{me.bank}")),       # withdraw from theirs
    ("to_account", lambda me, other: (f"portfolio:{me.main}", f"portfolio:{other.main}")),    # move into theirs
    ("bank", lambda me, other: (f"bank:{other.bank}", f"portfolio:{me.main}")),               # deposit from their bank
    ("bank out", lambda me, other: (f"portfolio:{me.main}", f"bank:{other.bank}")),           # withdraw to their bank
])
def test_another_customers_ids_in_a_transfer_are_refused(db, web_app, pair, field, pick):  # R2.5, R19.6
    me, other = pair
    before = counts(db), snapshot(db, me.user), snapshot(db, other.user)
    source, target = pick(me, other)
    assert transfer(as_customer(web_app, me), source, target).status_code == 404
    assert (counts(db), snapshot(db, me.user), snapshot(db, other.user)) == before
