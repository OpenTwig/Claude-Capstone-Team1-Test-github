"""Access control, generated from the app's URL map (task 5.3; R1, R2, R13.7, R18.3, D19, N2.3, P12, P17).

The routes come from `app.url_map`, so a new route can't be missed: the tests fail until it is added
to ACCESS (who may use it) and, if it takes an ID, to the ownership checks.
"""
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from flask import Flask

from app.crypto import encrypt_field
from app.migrate import APP_PRIVILEGES
from tests.factories import make_bank_account, make_portfolio, make_stock, make_user
from tests.web import csrf_token, login, make_login_user

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("America/Phoenix"))
PASSWORD = "correct horse battery"

# Who may use each endpoint. "any" = any signed-in user; the rest are refused on the server (R2.4, D19).
ACCESS = {
    "auth.signin": "public", "health": "public", "static": "public",
    "auth.home": "any", "auth.logout": "any", "auth.change_password": "any",
    "stocks.search_page": "any", "stocks.detail_page": "any", "history.history_page": "any",
    "account.summary": "customer", "account.create_portfolio": "customer", "account.positions_page": "customer",
    "account.set_reinvest": "customer",
    "trade.trade_page": "customer", "trade.quote_api": "customer", "trade.preview": "customer", "trade.confirm": "customer",
    "transfer.transfer_page": "customer", "transfer.submit_transfer": "customer", "transfer.add_bank_account": "customer",
    "transfer.remove_bank_account": "customer",
    "admin.index": "admin", "admin.add_user": "admin", "admin.edit_user": "admin", "admin.deactivate_user": "admin",
    "admin.issue_temporary_password": "admin", "admin.add_stock": "admin", "admin.edit_stock": "admin",
    "admin.delist_stock": "admin", "admin.save_schedule": "admin",
}


def routes(app: Flask):
    """(endpoint, method, url) for every route, with its path parameters filled in."""
    found = []
    for rule in app.url_map.iter_rules():
        values = {name: ("AURA" if name == "symbol" else "app.css" if name == "filename" else 999999)
                  for name in rule.arguments}
        url = rule.rule
        for name, value in values.items():
            url = re.sub(rf"<(?:[a-z]+:)?{name}>", str(value), url)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            found.append((rule.endpoint, method, "/static/css/app.css" if rule.endpoint == "static" else url))
    return found


def test_every_route_is_in_the_access_table(web_app):
    missing = sorted({endpoint for endpoint, _, _ in routes(web_app)} - set(ACCESS))
    assert not missing, f"add these routes to ACCESS (and to the ownership checks if they take IDs): {missing}"


@pytest.fixture
def people(db, web_app, fixed_clock):
    fixed_clock(NOW)
    make_login_user(db, PASSWORD, email="casey@example.test")
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    make_stock(db, symbol="AURA")


def fresh_login(client, email):
    """Log in without tripping the login rate limit (R1.9): these tests log in once per route, on purpose.
    The rate limit has its own test (test_auth_routes.test_login_attempts_are_rate_limited)."""
    from app.security import limiter

    limiter.reset()
    login(client, email, PASSWORD)


def client_for(web_app, role):
    client = web_app.test_client()
    if role != "anonymous":
        fresh_login(client, "admin@example.test" if role == "admin" else "casey@example.test")
    return client


def send(client, method, url, token_page):
    if method == "GET":
        return client.get(url)
    return client.post(url, data={"csrf_token": csrf_token(client, token_page)})


@pytest.mark.parametrize("role", ["anonymous", "customer", "admin"])
def test_route_by_role_matrix(db, web_app, people, role):  # R2.4, R2.10, D19
    token_page = {"anonymous": "/signin", "customer": "/account", "admin": "/admin"}[role]
    problems = []
    for endpoint, method, url in routes(web_app):
        rule = ACCESS[endpoint]
        if endpoint == "auth.logout":
            continue  # ends the session; covered by the session tests
        response = send(client_for(web_app, role), method, url, token_page)
        to_signin = response.status_code == 302 and response.headers["Location"].startswith("/signin")
        if response.status_code >= 500:
            problems.append(f"{method} {url}: server error {response.status_code}")
        elif role == "anonymous":
            if rule != "public" and not to_signin:
                problems.append(f"{method} {url}: anonymous got {response.status_code}, not a redirect to /signin")
        elif rule in ("customer", "admin") and rule != role:
            if response.status_code != 403:
                problems.append(f"{method} {url}: a {role} got {response.status_code}, not 403")
        elif response.status_code == 403 or to_signin:
            problems.append(f"{method} {url}: a {role} was refused ({response.status_code})")
    assert not problems, "\n".join(problems)


# --- Customer A with customer B's IDs: 404 everywhere (R2.5, R13.7) ----------------------------------------

@pytest.fixture
def two_customers(db, web_app, fixed_clock):
    fixed_clock(NOW)
    stock = make_stock(db, symbol="AURA")
    found = []
    for name in ("a", "b"):
        user = make_login_user(db, PASSWORD, email=f"{name}@example.test")
        portfolio = make_portfolio(db, user, account_name=f"{name.upper()} main", cash_balance=Decimal("500.00"))
        with web_app.app_context():
            bank = make_bank_account(db, user, account_number=encrypt_field(f"00012345678{len(found)}"))
        with db.cursor() as cur:
            cur.execute("INSERT INTO Inventory (portfolioID, issuerID, quantity, average_cost) VALUES (%s, %s, 1, 10)",
                        (portfolio, stock))
        db.commit()
        found.append(type("C", (), {"user": user, "portfolio": portfolio, "bank": bank, "email": f"{name}@example.test"}))
    return found[0], found[1], stock


def ownership_requests(a, b, stock):
    """Every request that takes a customer's ID, pointed at customer B's."""
    return [
        ("GET", f"/positions?portfolio={b.portfolio}", None),
        ("GET", f"/trade?portfolio={b.portfolio}", None),
        ("GET", f"/api/quote/AURA?portfolio={b.portfolio}", None),
        ("GET", f"/history?portfolio={b.portfolio}", None),
        ("POST", "/trade/preview", {"portfolio": b.portfolio, "symbol": "AURA", "action": "Sell", "unit": "Shares",
                                    "amount": "1", "order_type": "Market", "timing": "Day Only"}),
        ("POST", "/transfer", {"from_account": f"portfolio:{b.portfolio}", "to_account": f"bank:{a.bank}", "amount": "10",
                               "transfer_date": "2026-09-29"}),
        ("POST", "/transfer", {"from_account": f"bank:{b.bank}", "to_account": f"portfolio:{a.portfolio}", "amount": "10",
                               "transfer_date": "2026-09-29"}),
        ("POST", "/transfer", {"from_account": f"portfolio:{a.portfolio}", "to_account": f"portfolio:{b.portfolio}",
                               "amount": "10", "transfer_date": "2026-09-29"}),
        ("POST", f"/transfer/bank-accounts/{b.bank}/remove", {}),
        ("POST", f"/positions/{b.portfolio}/{stock}/reinvest", {"reinvest": "yes"}),
    ]


# Endpoints that take a customer's ID (in the path or the request), and so must be in ownership_requests.
OWNED_ENDPOINTS = {"account.positions_page", "trade.trade_page", "trade.quote_api", "history.history_page",
                   "trade.preview", "transfer.submit_transfer", "transfer.remove_bank_account", "account.set_reinvest"}


def test_every_customer_route_with_an_id_is_checked(web_app):
    with_ids = {endpoint for endpoint, _, url in routes(web_app)
                if ACCESS.get(endpoint) in ("customer", "any") and "999999" in url}
    assert with_ids <= OWNED_ENDPOINTS, f"add ownership checks for: {sorted(with_ids - OWNED_ENDPOINTS)}"


def counts(db):
    with db.cursor() as cur:
        result = {}
        for table in APP_PRIVILEGES:
            cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
            result[table] = cur.fetchone()["n"]
        cur.execute("SELECT SUM(cash_balance) AS cash, SUM(reinvest) AS reinvest, SUM(is_active) AS banks"
                    " FROM Portfolio, (SELECT SUM(reinvest) AS reinvest FROM Inventory) r, (SELECT SUM(is_active) AS is_active FROM BankAccount) b")
        result["state"] = cur.fetchone()
    db.commit()
    return result


def test_another_customers_ids_get_404(db, web_app, two_customers):  # R2.5, R13.7, R19.6
    a, b, stock = two_customers
    client = client_for(web_app, "customer")
    login(client, a.email, PASSWORD)
    before = counts(db)
    problems = []
    for method, url, data in ownership_requests(a, b, stock):
        if method == "GET":
            response = client.get(url)
        else:
            response = client.post(url, data={**data, "csrf_token": csrf_token(client, "/account")})
        if response.status_code != 404:
            problems.append(f"{method} {url} {data or ''}: {response.status_code}, not 404")
    assert not problems, "\n".join(problems)
    assert counts(db) == before


# --- While a password change is required, everything else redirects to it (R18.3) --------------------------

def test_a_required_password_change_blocks_every_other_route(db, web_app, fixed_clock):  # R18.3
    fixed_clock(NOW)
    user = make_login_user(db, PASSWORD, email="new@example.test")
    with db.cursor() as cur:
        cur.execute("UPDATE `User` SET must_change_password = TRUE WHERE userID = %s", (user,))
    db.commit()
    problems = []
    for endpoint, method, url in routes(web_app):
        if ACCESS[endpoint] == "public" or endpoint in ("auth.change_password", "auth.logout"):
            continue
        client = web_app.test_client()
        fresh_login(client, "new@example.test")
        response = send(client, method, url, "/change-password")
        if not (response.status_code == 302 and response.headers["Location"].endswith("/change-password")):
            problems.append(f"{method} {url}: {response.status_code}")
    assert not problems, "\n".join(problems)


# --- P12: every form carries a CSRF token, and every POST route needs one (N2.3) ----------------------------

POST_FORM = re.compile(r"<form\b[^>]*method=\"post\"[^>]*>(.*?)</form>", re.S | re.I)


def test_every_rendered_form_has_a_csrf_token(db, web_app, two_customers):  # P12
    a, b, stock = two_customers
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    pages = {
        "anonymous": ["/signin"],
        a.email: ["/account", f"/positions?portfolio={a.portfolio}", "/trade?symbol=AURA", "/transfer", "/history",
                  "/stocks", "/stocks/AURA"],
        "admin@example.test": ["/admin", "/history", "/stocks", "/stocks/AURA"],
    }
    forms_seen = 0
    for who, paths in pages.items():
        client = web_app.test_client()
        if who != "anonymous":
            login(client, who, PASSWORD)
        for path in paths:
            html = client.get(path).get_data(as_text=True)
            for form in POST_FORM.findall(html):
                forms_seen += 1
                assert 'name="csrf_token"' in form, f"a POST form on {path} has no CSRF token"
    # The review screen's Place order form, after a preview.
    client = web_app.test_client()
    login(client, a.email, PASSWORD)
    review = client.post("/trade/preview", data={"portfolio": a.portfolio, "symbol": "AURA", "action": "Buy", "unit": "Dollars",
                                                "amount": "5", "order_type": "Market", "timing": "Day Only",
                                                "csrf_token": csrf_token(client, "/trade")}).get_data(as_text=True)
    assert "Review Your Order" in review
    for form in POST_FORM.findall(review):
        forms_seen += 1
        assert 'name="csrf_token"' in form
    assert forms_seen >= 20   # sanity: the pages really had forms (sign in, log out, tickets, admin forms, ...)


@pytest.mark.parametrize("role", ["anonymous", "customer", "admin"])
def test_every_post_route_needs_a_csrf_token(db, web_app, people, role):  # P12, N2.3
    problems = []
    for endpoint, method, url in routes(web_app):
        if method != "POST":
            continue
        response = client_for(web_app, role).post(url, data={})
        if response.status_code != 400:
            problems.append(f"POST {url} without a token: {response.status_code}")
    assert not problems, "\n".join(problems)


# --- P17: SSNs are never shown, not even the last 4 digits (N1.3, D8) ---------------------------------------

def test_no_page_shows_any_part_of_an_ssn(db, web_app, fixed_clock):  # P17, R3.6, D8
    """Distinctive values (no other number on these pages shares their digits), on every page an SSN could reach."""
    fixed_clock(NOW)
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    admin = client_for(web_app, "admin")
    admin.post("/admin/users", data={"name": "Sam Secret", "ssn": "987-65-4329", "email": "sam@example.test",
                                     "date_of_birth": "02/03/1985", "role": "Customer", "csrf_token": csrf_token(admin, "/admin")})
    with db.cursor() as cur:
        cur.execute("SELECT userID FROM `User` WHERE email = 'sam@example.test'")
        sam = cur.fetchone()["userID"]
    db.commit()
    pages = [admin.get(p).get_data(as_text=True) for p in ("/admin", "/history", "/stocks")]
    pages.append(admin.post(f"/admin/users/{sam}/edit", data={"name": "", "email": "sam@example.test", "date_of_birth": "02/03/1985",
                                                              "csrf_token": csrf_token(admin, "/admin")}).get_data(as_text=True))
    for page in pages:
        for part in ("987-65-4329", "987654329", "65-4329", "4329", "987-65"):
            assert part not in page, part


def test_the_exposure_check_itself_fails_on_a_leak(web_app, no_sensitive_data_in_responses):
    """Proof that the suite-wide P17 check works: a page that shows an encrypted SSN makes the request fail."""
    with web_app.app_context():
        encrypt_field("987-65-4329")
    web_app.add_url_rule("/leak-for-test", "leak_for_test", lambda: "SSN on file: 987654329")
    web_app.view_functions["leak_for_test"].is_public = True
    with pytest.raises(AssertionError, match="P17"):
        web_app.test_client().get("/leak-for-test")
