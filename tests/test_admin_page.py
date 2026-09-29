"""Administration page (task 4.5; wf-6; R3, R14, R15, R18.9, R18.10, D8, D16, D19-D21)."""
import re
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from werkzeug.security import check_password_hash

from app.services import market
from tests.factories import make_portfolio, make_stock, make_user
from tests.web import csrf_token, login, make_login_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=AZ)
PASSWORD = "correct horse battery"


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def count(db, table, where="TRUE", params=()):
    return one(db, f"SELECT COUNT(*) AS n FROM `{table}` WHERE {where}", params)["n"]


@pytest.fixture
def admin(db, client, fixed_clock):
    fixed_clock(NOW)
    admin_id = make_login_user(db, PASSWORD, admin=True, email="admin@example.test", name="Ada Admin")
    login(client, "admin@example.test", PASSWORD)
    client.user_id = admin_id
    return client


def post(client, path, **data):
    data.setdefault("csrf_token", csrf_token(client, "/admin"))
    return client.post(path, data=data)


NEW_USER = {"name": "Casey Customer", "ssn": "123-45-6789", "email": "Casey@Example.test",
            "date_of_birth": "01/15/1990", "role": "Customer", "access_level": "", "department": ""}


def add_user(client, **overrides):
    return post(client, "/admin/users", **{**NEW_USER, **overrides})


def temp_password_in(html):
    return re.search(r"data-temp-password>([^<]+)<", html).group(1)


# --- The page and access (R2.4, D19) --------------------------------------------------------------------

def test_page_has_every_panel(db, admin):  # wf-6
    make_stock(db, symbol="AURA", company_name="Aurora Solar Works", price=D("42.18"))
    html = admin.get("/admin").get_data(as_text=True)
    for panel in ("Add User", "Add Stocks", "Market Time", "Users", "Stocks"):
        assert f">{panel}</h2>" in html, panel
    assert "data-market-time" in html and "Now open 9:00 AM to 5:00 PM Arizona time" in html
    assert 'data-stock-row="AURA"' in html and "$42.18" in html
    assert re.search(r'data-user-row="\d+"', html) and "Ada Admin" in html
    # Users and Stocks each take a full-width row (user feedback), not half the page each.
    assert re.search(r'<div class="col-12">\s*<section class="card"[^>]*data-users>', html)
    assert re.search(r'<div class="col-12">\s*<section class="card"[^>]*data-stocks>', html)


def test_users_list_never_shows_an_ssn(db, admin, web_app):  # R3.7, D8
    add_user(admin)
    html = admin.get("/admin").get_data(as_text=True)
    users = html[html.index("data-users"):html.index("data-stocks")]
    assert "Casey Customer" in users and "casey@example.test" in users and "01/15/1990" in users and "Customer" in users
    assert "SSN" not in users and "6789" not in html and "123-45-6789" not in html


@pytest.mark.parametrize("method, path", [
    ("get", "/admin"), ("post", "/admin/users"), ("post", "/admin/users/1/edit"), ("post", "/admin/users/1/deactivate"),
    ("post", "/admin/users/1/temporary-password"), ("post", "/admin/stocks"), ("post", "/admin/stocks/1/edit"),
    ("post", "/admin/stocks/1/delist"), ("post", "/admin/market-schedule"),
])
def test_customers_get_403_everywhere(db, client, fixed_clock, method, path):  # R2.4, D19
    fixed_clock(NOW)
    make_login_user(db, PASSWORD, email="casey@example.test")
    login(client, "casey@example.test", PASSWORD)
    kwargs = {"data": {"csrf_token": csrf_token(client, "/account")}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 403


def test_admin_page_needs_login(web_app):
    assert web_app.test_client().get("/admin").headers["Location"].startswith("/signin")


@pytest.mark.parametrize("path", ["/admin/users", "/admin/stocks", "/admin/market-schedule"])
def test_forms_need_a_csrf_token(db, admin, path):  # N2.3
    assert admin.post(path, data={}).status_code == 400


# --- Add User (R3.1-R3.6, R3.9) ------------------------------------------------------------------------------

def test_adding_a_customer_shows_the_temporary_password_once(db, admin, web_app):  # R3.4, R3.5, R18.1
    response = add_user(admin)
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert "Customer Casey Customer (casey@example.test) created." in html
    password = temp_password_in(html)
    assert html.count(password) == 1   # only in the notice: not in a flash message too
    user = one(db, "SELECT userID, password_hash, must_change_password, ssn, signup FROM `User` WHERE email = 'casey@example.test'")
    assert check_password_hash(user["password_hash"], password) and password not in user["password_hash"]  # only a hash (R3.5)
    assert user["must_change_password"] == 1 and user["signup"] is not None                                # R18.1, SR09
    assert "123-45-6789" not in user["ssn"]                                                                 # encrypted (R3.6)
    with admin.session_transaction() as session:                  # never in the session cookie or a flash message
        assert password not in repr(dict(session))
    assert password not in admin.get("/admin").get_data(as_text=True)  # not shown again
    assert count(db, "Admin") == 1                                       # a customer, not another admin


def test_adding_an_administrator(db, admin):  # R3.9, D19
    html = add_user(admin, role="Administrator", access_level="support", department="Help Desk",
                    email="sam@example.test", name="Sam Support").get_data(as_text=True)
    assert "Administrator Sam Support (sam@example.test) created." in html
    row = one(db, "SELECT a.access_level, a.department FROM Admin a JOIN `User` u ON u.userID = a.userID"
                  " WHERE u.email = 'sam@example.test'")
    assert (row["access_level"], row["department"]) == ("support", "Help Desk")
    assert count(db, "Portfolio") == 0  # administrators hold no portfolios


@pytest.mark.parametrize("overrides, field", [
    ({"name": ""}, "name"),
    ({"ssn": "123456789"}, "ssn"),
    ({"ssn": ""}, "ssn"),
    ({"email": "not-an-email"}, "email"),
    ({"date_of_birth": "02/30/1990"}, "date_of_birth"),   # an impossible date
    ({"date_of_birth": "1990-01-15"}, "date_of_birth"),
    ({"date_of_birth": "01/15/2030"}, "date_of_birth"),   # not in the past
    ({"role": "Owner"}, "role"),
    ({"role": "Administrator", "access_level": ""}, "access_level"),
])
def test_add_user_errors_create_nothing(db, admin, overrides, field):  # R3.1, R3.2
    before = count(db, "User")
    html = add_user(admin, **overrides).get_data(as_text=True)
    assert f'data-error="add_user:{field}"' in html
    assert count(db, "User") == before and "data-temp-password" not in html
    assert "123-45-6789" not in html   # an entered SSN is never echoed back (R3.6)


def test_duplicate_email_is_rejected(db, admin):  # R3.3
    add_user(admin)
    html = add_user(admin, email="CASEY@example.test").get_data(as_text=True)
    assert 'data-error="add_user:email"' in html and "A user with this email already exists." in html
    assert count(db, "User", "email = 'casey@example.test'") == 1


def test_the_form_keeps_what_was_typed_after_an_error(db, admin):
    html = add_user(admin, ssn="bad", role="Administrator", access_level="").get_data(as_text=True)
    assert 'value="Casey Customer"' in html and 'value="01/15/1990"' in html
    assert "<option selected>Administrator</option>" in html
    assert not re.search(r"data-admin-field hidden", html)   # the admin fields stay visible


# --- Edit, deactivate, and new temporary passwords (R3.10-R3.12, R18.9, R18.10) ---------------------------

def test_editing_a_user(db, admin):  # R3.12, D20
    user = make_user(db, name="Old Name", email="old@example.test", ssn="cipher")
    response = post(admin, f"/admin/users/{user}/edit", name="New Name", email="NEW@example.test",
                    date_of_birth="02/03/1985", ssn="999-99-9999", role="Administrator")
    assert response.status_code == 302
    row = one(db, "SELECT name, email, date_of_birth, ssn FROM `User` WHERE userID = %s", (user,))
    assert (row["name"], row["email"], row["date_of_birth"], row["ssn"]) == ("New Name", "new@example.test", date(1985, 2, 3), "cipher")
    assert count(db, "Admin", "userID = %s", (user,)) == 0  # the role can't be changed
    assert "Saved changes to New Name." in admin.get("/admin").get_data(as_text=True)


def test_edit_errors_keep_the_edit_form_open(db, admin):  # R3.12
    make_user(db, email="taken@example.test")
    user = make_user(db, name="Old Name", email="old@example.test")
    html = post(admin, f"/admin/users/{user}/edit", name="Old Name", email="taken@example.test",
                date_of_birth="01/01/1990").get_data(as_text=True)
    assert f'data-error="user-{user}:email"' in html and f'class="collapse show" id="edit-user-{user}"' in html
    assert one(db, "SELECT email FROM `User` WHERE userID = %s", (user,))["email"] == "old@example.test"


def test_deactivating_a_user_keeps_their_records(db, admin, client):  # R3.10, R3.11, D7
    add_user(admin)
    user = one(db, "SELECT userID FROM `User` WHERE email = 'casey@example.test'")["userID"]
    portfolio = make_portfolio(db, user)
    assert post(admin, f"/admin/users/{user}/deactivate").status_code == 302
    assert one(db, "SELECT is_active FROM `User` WHERE userID = %s", (user,))["is_active"] == 0
    assert count(db, "Portfolio", "portfolioID = %s", (portfolio,)) == 1
    html = admin.get("/admin").get_data(as_text=True)
    assert "was deactivated and can no longer log in" in html and "data-deactivated" in html


def test_a_deactivated_user_cannot_log_in(db, web_app, admin):  # R3.11
    make_login_user(db, PASSWORD, email="gone@example.test")
    user = one(db, "SELECT userID FROM `User` WHERE email = 'gone@example.test'")["userID"]
    post(admin, f"/admin/users/{user}/deactivate")
    other = web_app.test_client()
    assert "Invalid email or password" in login(other, "gone@example.test", PASSWORD).get_data(as_text=True)


def test_an_admin_cannot_deactivate_or_reset_themselves(db, admin):  # R3.10, R18.10
    messages = []
    for path in (f"/admin/users/{admin.user_id}/deactivate", f"/admin/users/{admin.user_id}/temporary-password"):
        assert post(admin, path).status_code == 302
        messages.append(admin.get("/admin").get_data(as_text=True).replace("&#39;", "'"))
    assert "You can't deactivate your own account." in messages[0]
    assert "You can't issue a temporary password to your own account." in messages[1]
    html = messages[1]
    me = one(db, "SELECT is_active, must_change_password FROM `User` WHERE userID = %s", (admin.user_id,))
    assert (me["is_active"], me["must_change_password"]) == (1, 0)
    my_row = html[html.index(f'data-user-row="{admin.user_id}"'):]
    my_row = my_row[:my_row.index("</tr>")]
    assert "Deactivate" not in my_row and "New temp password" not in my_row   # no buttons for yourself


def test_issuing_a_new_temporary_password(db, admin, web_app):  # R18.9
    make_login_user(db, PASSWORD, email="forgot@example.test")
    user = one(db, "SELECT userID, session_version FROM `User` WHERE email = 'forgot@example.test'")
    response = post(admin, f"/admin/users/{user['userID']}/temporary-password")
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    password = temp_password_in(html)
    assert html.count(password) == 1
    row = one(db, "SELECT password_hash, must_change_password, session_version FROM `User` WHERE userID = %s", (user["userID"],))
    assert check_password_hash(row["password_hash"], password) and row["must_change_password"] == 1
    assert row["session_version"] == user["session_version"] + 1          # existing logins ended
    with admin.session_transaction() as session:
        assert password not in repr(dict(session))
    other = web_app.test_client()
    assert "Invalid email or password" in login(other, "forgot@example.test", PASSWORD).get_data(as_text=True)


@pytest.mark.parametrize("path", ["/admin/users/999999/edit", "/admin/users/999999/deactivate",
                                  "/admin/users/999999/temporary-password"])
def test_unknown_users_are_404(db, admin, path):
    assert post(admin, path, name="X", email="x@example.test", date_of_birth="01/01/1990").status_code == 404


# --- Add Stocks, edit, delist (R14, D16, D20) --------------------------------------------------------------

NEW_STOCK = {"company_name": "Aurora Solar Works", "symbol": "aura", "price": "42.18", "shares": "1000000",
             "exchange": "NASDAQ", "description": "Solar panels.", "randomize_history": "on"}


def add_stock(client, **overrides):
    data = {**NEW_STOCK, **overrides}
    return post(client, "/admin/stocks", **{k: v for k, v in data.items() if v is not None})


def test_adding_a_stock_with_history(db, admin):  # R14.2, R14.5, R14.6, D16
    response = add_stock(admin)
    assert response.status_code == 302
    stock = one(db, "SELECT issuerID, ticker_symbol, current_price, shares_available, exchange, description, is_listed"
                    " FROM Issuer WHERE ticker_symbol = 'AURA'")
    assert (stock["current_price"], stock["shares_available"], stock["exchange"], stock["is_listed"]) \
        == (D("42.18"), D("1000000"), "NASDAQ", 1)
    assert 240 <= count(db, "StockPrice", "issuerID = %s", (stock["issuerID"],)) <= 260
    html = admin.get("/admin").get_data(as_text=True)
    assert "Stock AURA added with a year of price history. It can be traded now." in html
    assert 'data-stock-row="AURA"' in html and "1,000,000" in html


def test_numbers_with_thousands_separators(db, admin):  # D27 (user request: "1,000" shares was refused)
    assert add_stock(admin, price="1,234.50", shares="1,000,000.5").status_code == 302
    row = one(db, "SELECT current_price, shares_available FROM Issuer WHERE ticker_symbol = 'AURA'")
    assert (row["current_price"], row["shares_available"]) == (D("1234.50"), D("1000000.5"))


def test_adding_a_stock_without_history(db, admin):  # A9
    add_stock(admin, randomize_history=None)
    issuer = one(db, "SELECT issuerID FROM Issuer WHERE ticker_symbol = 'AURA'")["issuerID"]
    assert count(db, "StockPrice", "issuerID = %s", (issuer,)) == 1


@pytest.mark.parametrize("overrides, field, message", [
    ({"company_name": ""}, "company_name", "Stock name"),
    ({"symbol": "TOO-LONG-SYMBOL"}, "symbol", "Symbol must be"),
    ({"price": "0"}, "price", "greater than $0.00"),
    ({"price": "abc"}, "price", "Enter an amount"),
    ({"price": "100000000"}, "price", "The largest amount you can enter is $99,999,999.99."),
    ({"shares": "0"}, "shares", "greater than 0"),
    ({"shares": "1.1234567"}, "shares", "6 decimal places"),
    ({"description": "x" * 51}, "description", "Description"),
    ({"exchange": "x" * 11}, "exchange", "Exchange"),
])
def test_add_stock_errors_create_nothing(db, admin, overrides, field, message):  # R14.2, R14.3, A15
    html = add_stock(admin, **overrides).get_data(as_text=True)
    assert f'data-error="add_stock:{field}"' in html and message in html
    assert count(db, "Issuer") == 0


def test_duplicate_symbol(db, admin):  # R14.4
    make_stock(db, symbol="AURA")
    html = add_stock(admin).get_data(as_text=True)
    assert 'data-error="add_stock:symbol">Symbol already exists<' in html and count(db, "Issuer") == 1
    assert 'value="Aurora Solar Works"' in html   # the form keeps what was typed


def test_editing_a_stock(db, admin):  # R14.8, D20
    issuer = make_stock(db, symbol="AURA", company_name="Old", price=D("10.00"))
    assert post(admin, f"/admin/stocks/{issuer}/edit", company_name="New Name", description="New text.", exchange="NYSE",
                symbol="ZZZ", price="1.00").status_code == 302
    row = one(db, "SELECT company_name, description, exchange, ticker_symbol, current_price FROM Issuer WHERE issuerID = %s", (issuer,))
    assert (row["company_name"], row["description"], row["exchange"]) == ("New Name", "New text.", "NYSE")
    assert (row["ticker_symbol"], row["current_price"]) == ("AURA", D("10.00"))   # symbol and price can't change


def test_edit_stock_errors(db, admin):
    issuer = make_stock(db, symbol="AURA", company_name="Old")
    html = post(admin, f"/admin/stocks/{issuer}/edit", company_name="", description="", exchange="").get_data(as_text=True)
    assert f'data-error="stock-{issuer}:company_name"' in html and f'class="collapse show" id="edit-stock-{issuer}"' in html
    assert one(db, "SELECT company_name FROM Issuer WHERE issuerID = %s", (issuer,))["company_name"] == "Old"


def test_delisting_a_stock(db, admin):  # R14.9, D20
    issuer = make_stock(db, symbol="AURA")
    assert post(admin, f"/admin/stocks/{issuer}/delist").status_code == 302
    assert one(db, "SELECT is_listed FROM Issuer WHERE issuerID = %s", (issuer,))["is_listed"] == 0
    html = admin.get("/admin").get_data(as_text=True)
    row = html[html.index('data-stock-row="AURA"'):]
    row = row[:row.index("</tr>")]
    assert "data-delisted" in row and "Delist</button>" not in row
    assert post(admin, "/admin/stocks/999999/delist").status_code == 404


def test_stocks_list_shows_change_percent(db, admin):  # R14.7
    issuer = make_stock(db, symbol="AURA", price=D("42.00"))
    one(db, "INSERT INTO StockPrice (issuerID, price_date, open_price, close_price) VALUES (%s, '2026-09-28', 40, 40)", (issuer,))
    html = admin.get("/admin").get_data(as_text=True)
    row = html[html.index('data-stock-row="AURA"'):]
    assert "+5.00%" in row[:row.index("</tr>")]


# --- Market Time (R15.3-R15.5, D21) ----------------------------------------------------------------------

def test_saving_market_hours(db, admin, web_app):  # R15.3, R15.4
    assert post(admin, "/admin/market-schedule", open_time="08:30", close_time="16:00").status_code == 302
    with web_app.app_context():
        schedule = market.current_schedule()
        assert (schedule.open_time, schedule.close_time) == (time(8, 30), time(16, 0))
        assert market.is_market_open(datetime(2026, 9, 29, 8, 45, tzinfo=AZ))
        assert not market.is_market_open(datetime(2026, 9, 29, 16, 15, tzinfo=AZ))
    assert "Market hours saved: 8:30 AM to 4:00 PM Arizona time, every trading day." in admin.get("/admin").get_data(as_text=True)


@pytest.mark.parametrize("open_time, close_time, field", [
    ("16:00", "09:00", "close_time"), ("09:00", "09:00", "close_time"),
    ("", "17:00", "open_time"), ("09:00", "", "close_time"), ("9am", "17:00", "open_time"),
])
def test_invalid_hours_keep_the_current_schedule(db, admin, web_app, open_time, close_time, field):  # R15.5
    html = post(admin, "/admin/market-schedule", open_time=open_time, close_time=close_time).get_data(as_text=True)
    assert f'data-error="schedule:{field}"' in html
    with web_app.app_context():
        schedule = market.current_schedule()
        assert (schedule.open_time, schedule.close_time) == (time(9, 0), time(17, 0))


# --- Stocks list pages (user feedback: the list mustn't grow without end) ------------------------------

def stocks_on(html):
    return re.findall(r'data-stock-row="(\w+)"', html)


@pytest.fixture
def many_stocks(db):
    """25 listed stocks S01..S25 and 2 delisted ones (listed come first, then by symbol)."""
    for n in range(1, 26):
        make_stock(db, symbol=f"S{n:02d}")
    make_stock(db, symbol="AAGONE", is_listed=False)
    make_stock(db, symbol="ZZGONE", is_listed=False)


def test_the_stocks_list_shows_10_a_page(db, admin, many_stocks):
    html = admin.get("/admin").get_data(as_text=True)
    assert stocks_on(html) == [f"S{n:02d}" for n in range(1, 11)]
    assert "Showing 1–10 of 27" in html
    assert re.findall(r"data-page-current>(\d+)<", html) == ["1"]
    assert re.findall(r"data-page-link>(\d+)<", html) == ["2", "3"]
    assert 'href="/admin?users_page=1&amp;stocks_page=2#stocks" aria-label="Next page"' in html
    assert "data-page-prev" not in html                      # nothing before page 1


def test_later_pages(db, admin, many_stocks):
    page2 = admin.get("/admin?stocks_page=2").get_data(as_text=True)
    assert stocks_on(page2) == [f"S{n:02d}" for n in range(11, 21)] and "Showing 11–20 of 27" in page2
    assert 'href="/admin?users_page=1&amp;stocks_page=1#stocks" aria-label="Previous page"' in page2
    page3 = admin.get("/admin?stocks_page=3").get_data(as_text=True)
    assert stocks_on(page3) == ["S21", "S22", "S23", "S24", "S25", "AAGONE", "ZZGONE"]  # delisted last
    assert "Showing 21–27 of 27" in page3 and "data-page-next" not in page3


@pytest.mark.parametrize("asked, shown", [("99", "3"), ("0", "1"), ("-2", "1"), ("abc", "1"), ("", "1")])
def test_a_page_that_does_not_exist_shows_the_nearest_one(db, admin, many_stocks, asked, shown):
    html = admin.get(f"/admin?stocks_page={asked}").get_data(as_text=True)
    assert re.findall(r"data-page-current>(\d+)<", html) == [shown]


def test_no_selector_with_one_page(db, admin):
    make_stock(db, symbol="AURA")
    html = admin.get("/admin").get_data(as_text=True)
    stocks_footer = html[html.index('data-pages="stocks"'):]
    assert "Showing 1–1 of 1" in stocks_footer and "pagination" not in stocks_footer


def test_edit_and_delist_come_back_to_the_same_page(db, admin, many_stocks):
    issuer = one(db, "SELECT issuerID FROM Issuer WHERE ticker_symbol = 'S15'")["issuerID"]
    response = post(admin, f"/admin/stocks/{issuer}/edit", company_name="Renamed", description="", exchange="", stocks_page="2")
    assert response.headers["Location"] == "/admin?users_page=1&stocks_page=2#stocks"
    response = post(admin, f"/admin/stocks/{issuer}/delist", stocks_page="2", users_page="1")
    assert response.headers["Location"] == "/admin?users_page=1&stocks_page=2#stocks"
    html = post(admin, f"/admin/stocks/{issuer}/edit", company_name="", description="", exchange="", stocks_page="3").get_data(as_text=True)
    assert re.findall(r"data-page-current>(\d+)<", html) == ["3"] and f'id="edit-stock-{issuer}"' in html


@pytest.mark.parametrize("page, pages, shown", [
    (1, 1, [1]),
    (1, 3, [1, 2, 3]),
    (1, 12, [1, 2, None, 12]),
    (6, 12, [1, None, 5, 6, 7, None, 12]),
    (12, 12, [1, None, 11, 12]),
    (3, 5, [1, 2, 3, 4, 5]),
])
def test_page_numbers(page, pages, shown):
    from app.services.paging import page_numbers
    assert page_numbers(page, pages) == shown


# --- Users list pages (user feedback) -----------------------------------------------------------------

@pytest.fixture
def many_users(db, admin):
    """The admin (Ada Admin) plus 22 customers User 01..User 22: 23 users, sorted by name."""
    for n in range(1, 23):
        make_user(db, name=f"User {n:02d}", email=f"user{n:02d}@example.test")


def users_on(html):
    """The names in the Users list, in order (the first cell's text, before any badge)."""
    return re.findall(r'data-user-row="\d+"[^>]*>\s*<td>([^<\n]+?)\s*(?:<|\n)', html)


def test_the_users_list_shows_10_a_page(db, admin, many_users):
    html = admin.get("/admin").get_data(as_text=True)
    assert users_on(html) == ["Ada Admin"] + [f"User {n:02d}" for n in range(1, 10)]
    footer = html[html.index('data-pages="users"'):html.index("data-stocks")]
    assert "Showing 1–10 of 23" in footer and re.findall(r"data-page-link>(\d+)<", footer) == ["2", "3"]
    assert 'href="/admin?users_page=2&amp;stocks_page=1#users" aria-label="Next page"' in footer
    page3 = admin.get("/admin?users_page=3").get_data(as_text=True)
    assert users_on(page3) == ["User 20", "User 21", "User 22"]
    assert "Showing 21–23 of 23" in page3[page3.index('data-pages="users"'):]


def test_each_list_keeps_the_others_page(db, admin, many_users, many_stocks):
    html = admin.get("/admin?users_page=2&stocks_page=3").get_data(as_text=True)
    users_footer = html[html.index('data-pages="users"'):html.index("data-stocks")]
    assert 'href="/admin?users_page=3&amp;stocks_page=3#users"' in users_footer       # next users page, stocks stay on 3
    stocks_footer = html[html.index('data-pages="stocks"'):]
    assert 'href="/admin?users_page=2&amp;stocks_page=2#stocks"' in stocks_footer     # previous stocks page, users stay on 2


def test_user_actions_come_back_to_the_same_pages(db, admin, many_users):
    user = one(db, "SELECT userID FROM `User` WHERE name = 'User 15'")["userID"]
    response = post(admin, f"/admin/users/{user}/edit", name="User 15", email="user15@example.test",
                    date_of_birth="01/01/1990", users_page="2", stocks_page="1")
    assert response.headers["Location"] == "/admin?users_page=2&stocks_page=1#users"
    response = post(admin, f"/admin/users/{user}/deactivate", users_page="2", stocks_page="1")
    assert response.headers["Location"] == "/admin?users_page=2&stocks_page=1#users"
    html = post(admin, f"/admin/users/{user}/temporary-password", users_page="2").get_data(as_text=True)
    assert "data-temp-password" in html and users_on(html)[0] == "User 10"   # still showing page 2


def test_a_users_page_that_does_not_exist_shows_the_nearest_one(db, admin, many_users):
    html = admin.get("/admin?users_page=40").get_data(as_text=True)
    assert "Showing 21–23 of 23" in html[html.index('data-pages="users"'):]
