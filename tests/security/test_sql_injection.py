"""SQL injection through every text input on every route (task 5.1; N2.1, N2.2, P15).

Each payload in payloads.py goes into each field. For every request: no 500 error, no login bypass,
no response slower than 2 seconds (an injected SLEEP would take 5), and the table row counts change
only by the insert the form is meant to make. A value that is accepted is stored exactly as typed
(apart from the leading and trailing spaces every text field trims).
"""
import time
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest

from app.migrate import APP_PRIVILEGES
from tests.factories import make_portfolio, make_stock
from tests.security.payloads import PAYLOADS, SLOW_SECONDS
from tests.web import csrf_token, login, make_login_user

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("America/Phoenix"))
PASSWORD = "correct horse battery"
NAMES = list(PAYLOADS)


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def counts(db) -> dict[str, int]:
    return {table: one(db, f"SELECT COUNT(*) AS n FROM `{table}`")["n"] for table in APP_PRIVILEGES}


def timed(send):
    """Send a request; fail on a server error or a suspiciously slow answer."""
    started = time.perf_counter()
    response = send()
    elapsed = time.perf_counter() - started
    assert response.status_code < 500, f"server error {response.status_code}"
    assert elapsed < SLOW_SECONDS, f"took {elapsed:.1f}s: an injected SLEEP may have run"
    return response


def changed(before: dict, after: dict) -> dict:
    return {t: after[t] - before[t] for t in before if after[t] != before[t]}


def stored_as_typed(db, before, after, table, column, where, payload):
    """Either the form refused the value (nothing written), or it wrote one row holding exactly the value."""
    diff = changed(before, after)
    if not diff:
        return
    assert diff == {table: 1}, diff
    row = one(db, f"SELECT `{column}` AS v FROM `{table}` WHERE {where}")
    assert row is not None and row["v"] == payload.strip(), (row, payload[:40])


@pytest.fixture
def clocked(fixed_clock):
    fixed_clock(NOW)


@pytest.fixture
def customer(db, client, clocked):
    user = make_login_user(db, PASSWORD, email="casey@example.test")
    portfolio = make_portfolio(db, user, account_name="Brokerage", cash_balance=Decimal("1000.00"))
    login(client, "casey@example.test", PASSWORD)
    client.user_id, client.portfolio = user, portfolio
    return client


@pytest.fixture
def admin(db, client, clocked):
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    login(client, "admin@example.test", PASSWORD)
    return client


# --- Login (R1): no bypass -------------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_login_cannot_be_bypassed(db, web_app, clocked, name):  # N2.1, R1
    make_login_user(db, PASSWORD, email="casey@example.test")
    payload = PAYLOADS[name]
    for email, password in ((payload, payload), ("casey@example.test", payload), (payload, PASSWORD)):
        client = web_app.test_client()
        before = counts(db)
        response = timed(lambda: client.post("/signin", data={"email": email, "password": password,
                                                              "csrf_token": csrf_token(client)}))
        assert response.status_code in (200, 400, 429), response.status_code  # refused, never a redirect inside
        assert client.get("/account").headers.get("Location", "").startswith("/signin")  # still signed out
        diff = changed(before, counts(db))
        assert set(diff) <= {"User"} and not diff, diff  # a failed login only updates a counter, never adds rows


# --- Reading pages: search, quote, trade symbol, history filter ------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_read_only_inputs(db, customer, name):  # N2.1, P15
    """Nothing matches a payload, so each page's result area must stay empty (a leak would list AURA)."""
    make_stock(db, symbol="AURA", company_name="Aurora Solar Works")
    encoded = quote(PAYLOADS[name], safe="")
    before = counts(db)

    search = timed(lambda: customer.get(f"/stocks?q={encoded}")).get_data(as_text=True)
    if "data-results" in search:
        results = search[search.index("data-results"):search.index('data-panel="gainers"')]
        assert "data-symbol=" not in results
    else:
        assert 'data-error="q"' in search   # too long to search

    assert timed(lambda: customer.get(f"/stocks/{encoded}")).status_code == 404
    trade = timed(lambda: customer.get(f"/trade?symbol={encoded}")).get_data(as_text=True)
    assert "data-quote-name></h2>" in trade and "Symbol not found" in trade
    assert timed(lambda: customer.get(f"/api/quote/{encoded}?portfolio={customer.portfolio}")).status_code == 404
    history = timed(lambda: customer.get(f"/history?symbol={encoded}")).get_data(as_text=True)
    assert "data-history-row" not in history
    summary = timed(lambda: customer.get(f"/account?q={encoded}")).get_data(as_text=True)
    assert 'data-search="Brokerage' not in summary   # the portfolio search filtered it out: no match
    assert counts(db) == before


@pytest.mark.parametrize("name", NAMES)
def test_trade_ticket_symbol(db, customer, name):  # N2.1
    before = counts(db)
    timed(lambda: customer.post("/trade/preview", data={
        "portfolio": customer.portfolio, "symbol": PAYLOADS[name], "action": "Buy", "unit": "Dollars", "amount": "10",
        "order_type": "Market", "timing": "Day Only", "csrf_token": csrf_token(customer, "/trade")}))
    assert counts(db) == before


def test_search_treats_percent_and_underscore_literally(db, customer):  # P15
    make_stock(db, symbol="PCT", company_name="Percent%Corp")
    make_stock(db, symbol="UND", company_name="Under_score Inc")
    make_stock(db, symbol="PLN", company_name="Plain Corp")
    def found(query):
        html = customer.get(f"/stocks?q={quote(query, safe='')}").get_data(as_text=True)
        results = html[html.index("data-results"):html.index('data-panel="gainers"')]
        return {s for s in ("PCT", "UND", "PLN") if f'data-symbol="{s}"' in results}
    assert found("%") == {"PCT"}
    assert found("_") == {"UND"}
    assert found("%Corp") == {"PCT"}


# --- Forms that store text -------------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_portfolio_name(db, customer, name):  # R4.2
    payload = PAYLOADS[name]
    before = counts(db)
    timed(lambda: customer.post("/account/portfolios", data={
        "account_name": payload, "account_type": "Brokerage", "csrf_token": csrf_token(customer, "/account")}))
    stored_as_typed(db, before, counts(db), "Portfolio", "account_name", "portfolioID = (SELECT MAX(portfolioID) FROM Portfolio)", payload)


@pytest.mark.parametrize("field", ["bank_name", "nickname", "account_number", "routing_number"])
@pytest.mark.parametrize("name", NAMES)
def test_bank_account_fields(db, customer, name, field):  # R10.1
    payload = PAYLOADS[name]
    data = {"bank_name": "First Bank", "account_number": "000123456789", "routing_number": "021000021",
            "nickname": "Checking", field: payload, "csrf_token": csrf_token(customer, "/transfer")}
    before = counts(db)
    timed(lambda: customer.post("/transfer/bank-accounts", data=data))
    if field in ("account_number", "routing_number"):
        assert counts(db) == before   # digits only: every payload is refused
    else:
        stored_as_typed(db, before, counts(db), "BankAccount", field,
                        "bankAccountID = (SELECT MAX(bankAccountID) FROM BankAccount)", payload)


@pytest.mark.parametrize("field", ["name", "email"])
@pytest.mark.parametrize("name", NAMES)
def test_admin_add_user(db, admin, name, field):  # R3.1
    payload = PAYLOADS[name]
    data = {"name": "Casey Customer", "ssn": "123-45-6789", "email": "new.user@example.test", "date_of_birth": "01/15/1990",
            "role": "Customer", field: payload, "csrf_token": csrf_token(admin, "/admin")}
    before = counts(db)
    timed(lambda: admin.post("/admin/users", data=data))
    if field == "email":
        assert counts(db) == before   # none of the payloads is a valid email address
    else:
        stored_as_typed(db, before, counts(db), "User", "name", "email = 'new.user@example.test'", payload)


@pytest.mark.parametrize("field", ["company_name", "symbol", "description", "exchange"])
@pytest.mark.parametrize("name", NAMES)
def test_admin_add_stock(db, admin, name, field):  # R14.2
    payload = PAYLOADS[name]
    data = {"company_name": "Aurora Solar Works", "symbol": "AURA", "price": "42.18", "shares": "1000",
            "exchange": "NASDAQ", "description": "Solar.", field: payload, "csrf_token": csrf_token(admin, "/admin")}
    before = counts(db)
    timed(lambda: admin.post("/admin/stocks", data=data))
    after = counts(db)
    if field == "symbol":
        assert after == before   # letters and digits only: every payload is refused
        return
    diff = changed(before, after)
    if diff:  # accepted: one stock plus its price row(s), holding the value exactly
        assert set(diff) == {"Issuer", "StockPrice"} and diff["Issuer"] == 1, diff
        assert one(db, f"SELECT `{field}` AS v FROM Issuer WHERE ticker_symbol = 'AURA'")["v"] == payload.strip()


@pytest.mark.parametrize("name", NAMES)
def test_admin_edits(db, admin, name):  # R3.12, R14.8
    payload = PAYLOADS[name]
    stock = make_stock(db, symbol="AURA", company_name="Old Name")
    user = one(db, "SELECT userID FROM `User` WHERE email = 'admin@example.test'")["userID"]
    before = counts(db)
    timed(lambda: admin.post(f"/admin/stocks/{stock}/edit", data={
        "company_name": payload, "description": payload, "exchange": "", "csrf_token": csrf_token(admin, "/admin")}))
    timed(lambda: admin.post(f"/admin/users/{user}/edit", data={
        "name": payload, "email": "admin@example.test", "date_of_birth": "01/01/1980", "csrf_token": csrf_token(admin, "/admin")}))
    assert counts(db) == before   # edits never add or remove rows
    # Each edit was either refused (the old value stays) or saved exactly as typed.
    stock_name = one(db, "SELECT company_name AS v FROM Issuer WHERE issuerID = %s", (stock,))["v"]
    assert stock_name in ("Old Name", payload.strip()), stock_name
    user_name = one(db, "SELECT name AS v FROM `User` WHERE userID = %s", (user,))["v"]
    assert user_name in ("Test Customer", payload.strip()), user_name


def test_the_user_table_is_still_there(db, admin):  # the DROP TABLE payload did nothing
    for payload in PAYLOADS.values():
        admin.get(f"/stocks?q={quote(payload, safe='')}")
    assert one(db, "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'User'")["n"] == 1
