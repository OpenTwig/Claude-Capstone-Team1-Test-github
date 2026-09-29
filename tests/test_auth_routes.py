"""Login pages, sessions, navigation, and roles through real HTTP requests (task 2.1; R1, R2, R3.10, P10)."""
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import pytest

from app import create_app
from app.services import users
from tests.web import csrf_token, email_of, login, logout, make_login_user, set_cookies
from tests.conftest import STRONG_SECRET_KEY

pytestmark = pytest.mark.db

PASSWORD = "correct horse battery"


@pytest.fixture
def customer(db):
    return make_login_user(db, PASSWORD, name="Casey Customer", email="casey@example.test")


@pytest.fixture
def admin(db):
    return make_login_user(db, PASSWORD, admin=True, name="Ada Admin", email="ada@example.test")


# --- R1.1: every page needs login -------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/account", "/admin"])
def test_pages_redirect_to_login_when_not_signed_in(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/signin")


def test_being_sent_to_the_login_page_shows_no_notice(client):
    """The login page speaks for itself; no "please log in" pop-up (user feedback, Checkpoint 2)."""
    client.get("/account")  # redirected to /signin
    html = client.get("/signin").get_data(as_text=True)
    assert 'role="alert"' not in html
    assert "log in to continue" not in html.lower()


@pytest.mark.parametrize("path", ["/signin", "/health", "/static/css/app.css"])
def test_public_pages_do_not_need_login(client, path):
    assert client.get(path).status_code == 200


def test_unknown_page_is_a_404_not_a_login_redirect(client):
    assert client.get("/no-such-page").status_code == 404


def test_every_registered_route_is_protected_unless_marked_public(web_app):
    """A new route can't be left open by accident: list what is public and check it's intended."""
    public = sorted(
        rule.rule for rule in web_app.url_map.iter_rules()
        if rule.endpoint == "static" or getattr(web_app.view_functions[rule.endpoint], "is_public", False)
    )
    assert public == ["/health", "/signin", "/static/<path:filename>"]


# --- R1.2, D3: the login page ------------------------------------------------------------------

def test_login_page_has_the_wireframe_fields(client):
    html = client.get("/signin").get_data(as_text=True)
    for expected in ('name="email"', 'name="password"', 'name="stay_signed_in"', ">Stay signed in<", 'value="Submit"', 'name="csrf_token"'):
        assert expected in html
    # D10 revised D3: the wireframe's first-login note is shown (R18.2).
    assert "New users will need to reset password upon first login." in html


def test_login_form_requires_a_csrf_token(client, customer):  # N2.3
    response = client.post("/signin", data={"email": "casey@example.test", "password": PASSWORD})
    assert response.status_code == 400


# --- R1.3, R1.6, R1.7: logging in lands by role -------------------------------------------------

def test_customer_lands_on_account(client, customer):  # R1.7
    response = login(client, "casey@example.test", PASSWORD)
    assert response.status_code == 302 and response.headers["Location"] == "/account"
    page = client.get("/account")
    assert page.status_code == 200 and "Casey Customer" in page.get_data(as_text=True)


def test_admin_lands_on_administration(client, admin):  # R1.6
    response = login(client, "ada@example.test", PASSWORD)
    assert response.headers["Location"] == "/admin"
    assert client.get("/admin").status_code == 200


def test_home_page_sends_each_role_to_its_landing_page(client, customer):
    login(client, "casey@example.test", PASSWORD)
    assert client.get("/").headers["Location"] == "/account"
    assert client.get("/signin").headers["Location"] == "/account"  # already signed in


def test_next_parameter_cannot_redirect_off_site(client, customer):
    token = csrf_token(client)
    for target in ("https://evil.example/", "//evil.example/", "/admin"):
        response = client.post(f"/signin?next={target}",
                               data={"email": "casey@example.test", "password": PASSWORD, "csrf_token": token})
        assert response.headers["Location"] == "/account"
        logout(client)
        token = csrf_token(client)


# --- R1.4, P10: failures look identical -----------------------------------------------------------

def failure_page(client, email, password):
    response = login(client, email, password)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    html = re.sub(r'name="csrf_token"[^>]*value="[^"]+"', "CSRF", html)
    return html.replace(email, "EMAIL")


def test_unknown_email_wrong_password_and_deactivated_user_look_the_same(db, client, customer):  # P10
    wrong_password = failure_page(client, "casey@example.test", "not the password")
    unknown_email = failure_page(client, "stranger@example.test", PASSWORD)
    other = make_login_user(db, PASSWORD, email="gone@example.test", is_active=False)
    deactivated = failure_page(client, email_of(db, other), PASSWORD)
    assert "Invalid email or password" in wrong_password
    assert wrong_password == unknown_email == deactivated


# --- R1.5: Stay Signed In -----------------------------------------------------------------------

def test_without_stay_signed_in_the_session_ends_with_the_browser(client, customer):
    cookies = set_cookies(login(client, "casey@example.test", PASSWORD))
    assert "remember_token" not in cookies
    assert "Expires" not in cookies["session"]  # a browser-session cookie


def test_stay_signed_in_keeps_the_login_for_thirty_days(client, customer):  # R1.5, A2
    cookies = set_cookies(login(client, "casey@example.test", PASSWORD, stay_signed_in=True))
    expires = parsedate_to_datetime(re.search(r"Expires=([^;]+)", cookies["remember_token"]).group(1))
    assert timedelta(days=29) < expires - datetime.now(timezone.utc) <= timedelta(days=30, minutes=1)
    assert "HttpOnly" in cookies["remember_token"]


def test_remember_cookie_alone_restores_the_login(web_app, client, customer):
    login(client, "casey@example.test", PASSWORD, stay_signed_in=True)
    remember = client.get_cookie("remember_token").value
    fresh = web_app.test_client()  # a new browser session: no session cookie, only the remember cookie
    fresh.set_cookie("remember_token", remember)
    assert fresh.get("/account").status_code == 200


# --- R1.8, R1.9: lockout and rate limiting ----------------------------------------------------------

def test_five_wrong_passwords_lock_the_account(client, customer):  # R1.8
    for _ in range(5):
        login(client, "casey@example.test", "wrong")
    html = login(client, "casey@example.test", PASSWORD).get_data(as_text=True)
    assert "Too many failed attempts. Try again in 15 minutes." in html


def test_login_attempts_are_rate_limited(client):  # R1.9
    token = csrf_token(client)
    statuses = [
        client.post("/signin", data={"email": f"u{i}@example.test", "password": "x", "csrf_token": token}).status_code
        for i in range(11)
    ]
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429


# --- R1.10: logout ---------------------------------------------------------------------------------

def test_logout_ends_the_session_and_forgets_the_login(client, customer):
    login(client, "casey@example.test", PASSWORD, stay_signed_in=True)
    response = logout(client)
    assert response.status_code == 302 and response.headers["Location"] == "/signin"
    cleared = set_cookies(response)["remember_token"]
    assert "remember_token=;" in cleared  # remember cookie emptied and expired
    assert client.get("/account").status_code == 302
    assert "You have been logged out." in client.get("/signin").get_data(as_text=True)


def test_logout_needs_post_with_a_csrf_token(client, customer):
    login(client, "casey@example.test", PASSWORD)
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 400
    assert client.get("/account").status_code == 200  # still logged in


# --- R1.11: cookie flags in production --------------------------------------------------------------

def test_login_cookies_are_secure_in_production(db, db_env, customer):
    db_env["APP_ENV"] = "production"
    db_env["SECRET_KEY"] = STRONG_SECRET_KEY  # production refuses the test suite's weak key (task 6.1)
    client = create_app(db_env).test_client()
    cookies = set_cookies(login(client, "casey@example.test", PASSWORD, stay_signed_in=True))
    for name in ("session", "remember_token"):
        assert "Secure" in cookies[name] and "HttpOnly" in cookies[name], name
    assert "SameSite=Lax" in cookies["session"]


# --- R3.10: deactivation ends existing sessions -------------------------------------------------------

def test_deactivated_user_is_signed_out_on_the_next_request(web_app, client, customer, admin):
    login(client, "casey@example.test", PASSWORD, stay_signed_in=True)
    remember = client.get_cookie("remember_token").value
    with web_app.app_context():
        users.deactivate_user(admin, customer)
    assert client.get("/account").status_code == 302
    fresh = web_app.test_client()
    fresh.set_cookie("remember_token", remember)
    assert fresh.get("/account").status_code == 302


# --- R2: navigation, roles, profile -------------------------------------------------------------------

NAV = ["Account", "Positions", "Trade", "Transfer Funds", "Stocks", "History"]


def nav_labels(html):
    return re.findall(r'class="nav-link(?: active)?" href="[^"]+"[^>]*>([^<]+)</a>', html)


def test_customer_navigation(client, customer):  # R2.1, R2.3
    login(client, "casey@example.test", PASSWORD)
    html = client.get("/account").get_data(as_text=True)
    assert nav_labels(html) == NAV
    assert 'href="/account" aria-current="page"' in html  # current page highlighted
    assert "Log out" in html and "Profile" in html


def test_admin_navigation_is_stocks_administration_history(client, admin):  # R2.2, D19
    login(client, "ada@example.test", PASSWORD)
    html = client.get("/admin").get_data(as_text=True)
    assert nav_labels(html) == ["Stocks", "Administration", "History"]
    assert 'href="/admin" aria-current="page"' in html


@pytest.mark.parametrize("method, path", [
    ("get", "/account"), ("get", "/transfer"), ("post", "/transfer"), ("post", "/transfer/bank-accounts"),
])
def test_administrators_are_refused_customer_pages(client, admin, method, path):  # R2.10, D19
    login(client, "ada@example.test", PASSWORD)
    data = {"csrf_token": csrf_token(client, "/admin")} if method == "post" else None
    response = getattr(client, method)(path, data=data)
    assert response.status_code == 403


def test_customers_still_reach_their_pages(client, customer):
    login(client, "casey@example.test", PASSWORD)
    assert client.get("/account").status_code == 200 and client.get("/transfer").status_code == 200


def test_customer_is_refused_administration_on_the_server(client, customer):  # R2.4
    login(client, "casey@example.test", PASSWORD)
    response = client.get("/admin")
    assert response.status_code == 403
    assert "You don&#39;t have permission to view this page." in response.get_data(as_text=True)


def test_profile_menu_for_a_customer(client, customer):  # R2.6
    login(client, "casey@example.test", PASSWORD)
    html = client.get("/account").get_data(as_text=True)
    assert 'data-profile="name">Casey Customer<' in html
    assert 'data-profile="email">casey@example.test<' in html
    assert 'data-profile="access-level"' not in html


def test_profile_menu_for_an_admin(client, admin):  # R2.7
    login(client, "ada@example.test", PASSWORD)
    html = client.get("/admin").get_data(as_text=True)
    assert "Access level: superadmin" in html and "Department: Operations" in html


def test_logged_out_pages_show_no_navigation(client):
    html = client.get("/signin").get_data(as_text=True)
    assert nav_labels(html) == [] and "Log out" not in html


# --- flask create-admin ----------------------------------------------------------------------------------

def test_create_admin_command(db, web_app):
    answers = "\n".join(["Grace Hopper", "Grace@Example.test", "12/09/1906", "123-45-6789",
                         "a-long-password", "a-long-password", "superadmin", "Engineering"]) + "\n"
    result = web_app.test_cli_runner().invoke(args=["create-admin"], input=answers)
    assert result.exit_code == 0, result.output
    assert "Administrator created" in result.output
    assert "123-45-6789" not in result.output  # SSN prompt is hidden
    client = web_app.test_client()
    assert login(client, "grace@example.test", "a-long-password").headers["Location"] == "/admin"


@pytest.mark.parametrize("dob, password, message", [
    ("1906-12-09", "a-long-password", "MM/DD/YYYY"),
    ("12/09/1906", "short", "at least 12 characters"),
])
def test_create_admin_command_rejects_bad_input(web_app, dob, password, message):
    answers = "\n".join(["Grace", "g@example.test", dob, "123-45-6789", password, password, "admin", ""]) + "\n"
    result = web_app.test_cli_runner().invoke(args=["create-admin"], input=answers)
    assert result.exit_code != 0 and message in result.output
