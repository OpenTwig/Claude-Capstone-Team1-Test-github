"""First-login password change and admin-issued temporary passwords (task 3.5; R18, D10, A18, N1.6)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.auth import load_user
from app.errors import AccountLocked, Forbidden, InvalidCredentials, NotFound, TemporaryPasswordExpired, ValidationFailed
from app.migrate import MIGRATIONS_DIR, split_sql
from app.services import users
from tests.web import csrf_token, login, logout, make_login_user, set_cookies

pytestmark = pytest.mark.db

NEW_PASSWORD = "a brand new passphrase"


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


# These tests mix direct service calls with requests through the test client. Never hold an app
# context open while using the client: Flask reuses it for every request, so flask.g (the cached
# CSRF token, the DB connection) leaks between requests. Service calls use the svc fixture (conftest).


@pytest.fixture
def admin(db):
    return make_login_user(db, "admin password 123", admin=True, email="ada@example.test")


@pytest.fixture
def new_customer(svc, admin):
    """A customer just created by an admin: (user_id, temporary password)."""
    return svc(users.create_user, admin, "Nina New", "123-45-6789", "nina@example.test", date(1995, 3, 3))


def change(client, new=NEW_PASSWORD, confirm=None):
    return client.post("/change-password", data={
        "new_password": new, "confirm_password": new if confirm is None else confirm,
        "csrf_token": csrf_token(client, "/change-password"),
    })


# --- R18.1: new users get a 7-day temporary password ---------------------------------------------

def test_new_user_must_change_a_seven_day_temporary_password(db, new_customer):  # R18.1, A18
    user_id, _ = new_customer
    row = one(db, "SELECT must_change_password, temp_password_expires_at FROM `User` WHERE userID = %s", (user_id,))
    assert row["must_change_password"] == 1
    expected = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7)
    assert abs(row["temp_password_expires_at"] - expected) < timedelta(seconds=30)


def test_admins_created_on_the_command_line_do_not_need_to_change(db, app_ctx):
    user_id = users.create_admin("Root", "111-22-3333", "root@example.test", date(1980, 1, 1),
                                 "an admin passphrase", "admin", None)
    assert users.get_user(user_id).must_change_password is False


def test_login_page_shows_the_first_login_note(client):  # R18.2
    assert "New users will need to reset password upon first login." in client.get("/signin").get_data(as_text=True)


# --- R18.3, R18.4: forced change at first login --------------------------------------------------

def test_first_login_goes_to_choose_a_new_password(client, new_customer):  # R18.3
    _, temp = new_customer
    response = login(client, "nina@example.test", temp)
    assert response.headers["Location"] == "/change-password"
    page = client.get("/change-password").get_data(as_text=True)
    assert "Choose a new password" in page and 'name="new_password"' in page and 'name="confirm_password"' in page


@pytest.mark.parametrize("path", ["/", "/account", "/admin"])  # later pages join as they are built
def test_every_other_page_redirects_until_the_password_is_changed(client, new_customer, path):  # R18.3
    login(client, "nina@example.test", new_customer[1])
    response = client.get(path)
    assert response.status_code == 302 and response.headers["Location"] == "/change-password"


def test_log_out_and_public_pages_still_work_during_a_forced_change(client, new_customer):  # R18.3
    login(client, "nina@example.test", new_customer[1])
    assert client.get("/health").status_code == 200
    response = logout(client, "/change-password")
    assert response.headers["Location"] == "/signin"


def test_navigation_hides_page_links_during_a_forced_change(client, new_customer):
    login(client, "nina@example.test", new_customer[1])
    html = client.get("/change-password").get_data(as_text=True)
    assert 'href="/account"' not in html and "Log out" in html


def test_stay_signed_in_is_ignored_for_a_temporary_password(client, new_customer):  # R18.4
    cookies = set_cookies(login(client, "nina@example.test", new_customer[1], stay_signed_in=True))
    assert "remember_token" not in cookies


# --- R18.5-R18.7: choosing the new password -------------------------------------------------------

@pytest.mark.parametrize("new, confirm, field, message", [
    (NEW_PASSWORD, "something else entirely", "confirm_password", "don&#39;t match"),
    ("short", None, "new_password", "at least 12 characters"),
    ("password1234", None, "new_password", "too common"),
    ("nina-rules-forever", None, "new_password", "email name"),  # N1.6
])
def test_invalid_new_passwords_are_rejected(db, client, new_customer, new, confirm, field, message):  # R18.6
    login(client, "nina@example.test", new_customer[1])
    response = change(client, new, confirm)
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and f'data-error="{field}"' in html and message in html
    assert one(db, "SELECT must_change_password FROM `User` WHERE userID = %s", (new_customer[0],))["must_change_password"] == 1


def test_the_temporary_password_cannot_be_reused(client, new_customer):  # R18.6
    _, temp = new_customer
    login(client, "nina@example.test", temp)
    html = change(client, temp).get_data(as_text=True)
    assert "different from your temporary one" in html


def test_changing_the_password(db, web_app, client, new_customer):  # R18.7
    user_id, temp = new_customer
    login(client, "nina@example.test", temp)
    response = change(client)
    assert response.headers["Location"] == "/account"
    page = client.get("/account")
    assert page.status_code == 200  # this browser stays signed in
    assert "Your password has been changed." in page.get_data(as_text=True)
    row = one(db, "SELECT must_change_password, temp_password_expires_at, session_version FROM `User` WHERE userID = %s",
              (user_id,))
    assert row == {"must_change_password": 0, "temp_password_expires_at": None, "session_version": 1}
    with web_app.app_context():
        with pytest.raises(InvalidCredentials):
            users.authenticate("nina@example.test", temp)  # the temporary password no longer works
        assert users.authenticate("nina@example.test", NEW_PASSWORD).user_id == user_id


def test_the_change_page_is_not_available_afterwards(client, new_customer):  # D10: no other way to change
    login(client, "nina@example.test", new_customer[1])
    change(client)
    assert client.get("/change-password").headers["Location"] == "/account"


def test_change_password_service_refuses_when_no_change_is_required(db, app_ctx):  # D10
    user_id = make_login_user(db)
    with pytest.raises(Forbidden):
        users.change_password(user_id, NEW_PASSWORD, NEW_PASSWORD)


def test_changing_the_password_ends_other_logins(web_app, new_customer):  # R18.7
    _, temp = new_customer
    first, second = web_app.test_client(), web_app.test_client()
    login(first, "nina@example.test", temp)
    login(second, "nina@example.test", temp)
    change(first)
    assert first.get("/account").status_code == 200
    assert second.get("/account").headers["Location"].startswith("/signin")


def test_login_ids_without_the_current_version_are_refused(db, app_ctx):  # R18.7
    user_id = make_login_user(db)
    assert load_user(f"{user_id}:0") is not None
    assert load_user(f"{user_id}:1") is None   # stale version
    assert load_user(str(user_id)) is None     # old format, no version
    assert load_user("not-an-id") is None


# --- R18.8: expiry ---------------------------------------------------------------------------------

def test_an_expired_temporary_password_is_refused_with_its_own_message(db, client, new_customer):  # R18.8
    user_id, temp = new_customer
    one(db, "UPDATE `User` SET temp_password_expires_at = UTC_TIMESTAMP(3) - INTERVAL 1 MINUTE WHERE userID = %s", (user_id,))
    html = login(client, "nina@example.test", temp).get_data(as_text=True)
    assert "Your temporary password has expired. Ask an administrator for a new one." in html
    wrong = login(client, "nina@example.test", "not the password").get_data(as_text=True)
    assert "Invalid email or password" in wrong and "expired" not in wrong  # only the right password reveals it


def test_expiry_is_exactly_seven_days(app_ctx, admin, fixed_clock):  # A18
    start = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    fixed_clock(start)
    _, temp = users.create_user(admin, "Eve", "123-45-6789", "eve@example.test", date(1990, 1, 1))
    assert users.authenticate("eve@example.test", temp, start + timedelta(days=7) - timedelta(seconds=1)).must_change_password
    with pytest.raises(TemporaryPasswordExpired):
        users.authenticate("eve@example.test", temp, start + timedelta(days=7))


def test_lockout_applies_to_temporary_passwords(new_customer, app_ctx):  # R18.11
    _, temp = new_customer
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            users.authenticate("nina@example.test", "wrong")
    with pytest.raises(AccountLocked):
        users.authenticate("nina@example.test", temp)


# --- R18.9, R18.10: new temporary passwords ----------------------------------------------------------

def test_admin_issues_a_new_temporary_password(db, web_app, svc, admin):  # R18.9
    user_id = make_login_user(db, "an old passphrase here", email="forgetful@example.test")
    one(db, "UPDATE `User` SET failed_login_count = 4, locked_until = UTC_TIMESTAMP(3) + INTERVAL 10 MINUTE"
            " WHERE userID = %s", (user_id,))
    browser = web_app.test_client()
    one(db, "UPDATE `User` SET locked_until = NULL WHERE userID = %s", (user_id,))
    login(browser, "forgetful@example.test", "an old passphrase here")
    one(db, "UPDATE `User` SET locked_until = UTC_TIMESTAMP(3) + INTERVAL 10 MINUTE WHERE userID = %s", (user_id,))

    temp = svc(users.issue_temporary_password, admin, user_id)

    row = one(db, "SELECT must_change_password, failed_login_count, locked_until FROM `User` WHERE userID = %s", (user_id,))
    assert row == {"must_change_password": 1, "failed_login_count": 0, "locked_until": None}  # lockout cleared
    assert browser.get("/account").headers["Location"].startswith("/signin")                  # old login ended
    with pytest.raises(InvalidCredentials):
        svc(users.authenticate, "forgetful@example.test", "an old passphrase here")              # old password gone
    assert svc(users.authenticate, "forgetful@example.test", temp).must_change_password         # back to first login


def test_new_temporary_password_ends_remember_cookies_too(db, web_app, svc, admin):  # R18.9
    user_id = make_login_user(db, "an old passphrase here", email="rem@example.test")
    browser = web_app.test_client()
    login(browser, "rem@example.test", "an old passphrase here", stay_signed_in=True)
    remember = browser.get_cookie("remember_token").value
    svc(users.issue_temporary_password, admin, user_id)
    fresh = web_app.test_client()
    fresh.set_cookie("remember_token", remember)
    assert fresh.get("/account").headers["Location"].startswith("/signin")


def test_admins_cannot_issue_themselves_a_temporary_password(app_ctx, admin):  # R18.10
    with pytest.raises(ValidationFailed):
        users.issue_temporary_password(admin, admin)


def test_only_administrators_can_issue_temporary_passwords(db, app_ctx, admin):
    customer = make_login_user(db)
    with pytest.raises(Forbidden):
        users.issue_temporary_password(customer, admin)
    with pytest.raises(NotFound):
        users.issue_temporary_password(admin, 999999)


def test_issue_temp_password_command(db, web_app, svc, admin):  # R18.10
    result = web_app.test_cli_runner().invoke(args=["issue-temp-password", "ADA@example.test"])
    assert result.exit_code == 0, result.output
    temp = result.output.splitlines()[1].strip()
    assert svc(users.authenticate, "ada@example.test", temp).must_change_password
    missing = web_app.test_cli_runner().invoke(args=["issue-temp-password", "nobody@example.test"])
    assert missing.exit_code != 0 and "No user has that email address." in missing.output


# --- Migration 002 --------------------------------------------------------------------------------

def test_migration_002_flags_existing_customers_but_not_administrators(db, admin):  # D10, decision 4
    customer = make_login_user(db)
    one(db, "UPDATE `User` SET must_change_password = FALSE, temp_password_expires_at = NULL")
    update = [s for s in split_sql((MIGRATIONS_DIR / "002_password_change.sql").read_text(encoding="utf-8"))
              if s.startswith("UPDATE")]
    assert len(update) == 1
    one(db, update[0])
    flags = {r_id: one(db, "SELECT must_change_password AS m, temp_password_expires_at AS e FROM `User` WHERE userID = %s",
                       (r_id,)) for r_id in (customer, admin)}
    assert flags[customer]["m"] == 1 and flags[customer]["e"] is not None
    assert flags[admin]["m"] == 0 and flags[admin]["e"] is None
