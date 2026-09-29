"""User service: login, lockout, and admin-managed accounts (task 2.1; R1, R3, P10, P11, D7, D8)."""
import dataclasses
from datetime import date, datetime, timedelta, timezone

import pytest
from werkzeug.security import check_password_hash

from app.crypto import decrypt_field
from app.errors import AccountLocked, Forbidden, InvalidCredentials, NotFound, ValidationFailed
from app.services import users
from app.services.types import UserRow
from tests.factories import make_portfolio
from tests.web import email_of, make_login_user

pytestmark = pytest.mark.db

PASSWORD = "correct horse battery"
T0 = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)


def row(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


@pytest.fixture
def customer(db, app_ctx):
    user_id = make_login_user(db, PASSWORD, name="Casey Customer", email="casey@example.test")
    return user_id


@pytest.fixture
def admin(db, app_ctx):
    return make_login_user(db, PASSWORD, admin=True, name="Ada Admin", email="ada@example.test")


# --- authenticate (R1.3, R1.4, R3.11, P10) ---------------------------------------------------

def test_correct_email_and_password_log_in(customer):  # R1.3
    user = users.authenticate("casey@example.test", PASSWORD, T0)
    assert user.user_id == customer and user.name == "Casey Customer" and not user.is_admin


def test_email_is_matched_case_insensitively_and_trimmed(customer):
    assert users.authenticate("  Casey@Example.TEST ", PASSWORD, T0).user_id == customer


@pytest.mark.parametrize("email, password", [
    ("casey@example.test", "wrong password"),   # wrong password
    ("nobody@example.test", PASSWORD),          # unknown email
    ("casey@example.test", ""),                 # empty password
])
def test_failures_all_raise_the_same_error(customer, email, password):  # R1.4, P10
    with pytest.raises(InvalidCredentials) as exc:
        users.authenticate(email, password, T0)
    assert exc.value.message == "Invalid email or password"


def test_deactivated_user_gets_the_same_error_even_with_the_right_password(db, customer):  # R3.11, P10
    row(db, "UPDATE `User` SET is_active = FALSE WHERE userID = %s", (customer,))
    with pytest.raises(InvalidCredentials):
        users.authenticate("casey@example.test", PASSWORD, T0)


# --- lockout (R1.8, A1, P11) -----------------------------------------------------------------

def fail(times, at=T0, email="casey@example.test"):
    for _ in range(times):
        with pytest.raises((InvalidCredentials, AccountLocked)):
            users.authenticate(email, "wrong password", at)


@pytest.mark.parametrize("failures", [0, 1, 2, 3, 4])
def test_fewer_than_five_failures_do_not_lock(db, customer, failures):  # P11
    fail(failures)
    assert users.authenticate("casey@example.test", PASSWORD, T0).user_id == customer
    assert row(db, "SELECT failed_login_count FROM `User` WHERE userID = %s", (customer,))["failed_login_count"] == 0


def test_five_failures_lock_the_account_for_fifteen_minutes(db, customer):  # R1.8, P11
    fail(5)
    locked = row(db, "SELECT locked_until FROM `User` WHERE userID = %s", (customer,))["locked_until"]
    assert locked == (T0 + timedelta(minutes=15)).replace(tzinfo=None)
    with pytest.raises(AccountLocked) as exc:
        users.authenticate("casey@example.test", PASSWORD, T0 + timedelta(minutes=1))
    assert exc.value.message == "Too many failed attempts. Try again in 15 minutes."
    with pytest.raises(AccountLocked):
        users.authenticate("casey@example.test", PASSWORD, T0 + timedelta(minutes=14, seconds=59))
    assert users.authenticate("casey@example.test", PASSWORD, T0 + timedelta(minutes=15)).user_id == customer


def test_attempts_during_a_lock_do_not_extend_it(db, customer):
    fail(5)
    fail(3, at=T0 + timedelta(minutes=10))  # still locked; these are refused without counting
    assert users.authenticate("casey@example.test", PASSWORD, T0 + timedelta(minutes=15)).user_id == customer


def test_a_successful_login_resets_the_failure_count(customer):  # P11
    fail(4)
    users.authenticate("casey@example.test", PASSWORD, T0)
    fail(4)
    assert users.authenticate("casey@example.test", PASSWORD, T0).user_id == customer


def test_after_the_lock_expires_counting_starts_again(customer):
    fail(5)
    later = T0 + timedelta(minutes=16)
    fail(4, at=later)
    assert users.authenticate("casey@example.test", PASSWORD, later).user_id == customer


# --- create_user (R3.1-R3.6, D2) ---------------------------------------------------------------

def test_admin_creates_a_customer_with_a_temporary_password(db, admin):  # R3.4, R3.5, D2
    user_id, temp = users.create_user(admin, " New Customer ", "123-45-6789", "New@Example.TEST", date(1990, 5, 17))
    stored = row(db, "SELECT * FROM `User` WHERE userID = %s", (user_id,))
    assert len(temp) >= 16
    assert stored["password_hash"] != temp and check_password_hash(stored["password_hash"], temp)
    assert stored["name"] == "New Customer" and stored["email"] == "new@example.test"
    assert stored["date_of_birth"] == date(1990, 5, 17)
    assert abs(stored["signup"] - datetime.now(timezone.utc).replace(tzinfo=None)) < timedelta(seconds=10)  # SR09
    assert row(db, "SELECT COUNT(*) AS n FROM Admin WHERE userID = %s", (user_id,))["n"] == 0
    assert users.authenticate("new@example.test", temp, T0).user_id == user_id


def test_ssn_is_stored_encrypted(db, admin):  # R3.6, N1.3
    user_id, _ = users.create_user(admin, "Sam", "123-45-6789", "sam@example.test", date(1990, 1, 1))
    stored = row(db, "SELECT ssn FROM `User` WHERE userID = %s", (user_id,))["ssn"]
    assert "123-45-6789" not in stored and "6789" not in stored
    assert decrypt_field(stored) == "123-45-6789"


def test_each_temporary_password_is_different(admin):
    _, first = users.create_user(admin, "A", "123-45-6789", "a@example.test", date(1990, 1, 1))
    _, second = users.create_user(admin, "B", "123-45-6789", "b@example.test", date(1990, 1, 1))
    assert first != second


@pytest.mark.parametrize(
    "field, overrides",
    [
        ("name", {"name": ""}),
        ("name", {"name": "   "}),
        ("name", {"name": "x" * 256}),
        ("ssn", {"ssn": "123456789"}),
        ("ssn", {"ssn": "123-456-789"}),
        ("ssn", {"ssn": "abc-de-fghi"}),
        ("ssn", {"ssn": ""}),
        ("email", {"email": ""}),
        ("email", {"email": "no-at-sign"}),
        ("email", {"email": "a@b"}),
        ("email", {"email": "a b@example.test"}),
        ("email", {"email": "x" * 310 + "@example.test"}),
        ("date_of_birth", {"dob": None}),
        ("date_of_birth", {"dob": date.today() + timedelta(days=1)}),
        ("date_of_birth", {"dob": date(1899, 12, 31)}),
    ],
)
def test_invalid_input_is_rejected_and_nothing_is_created(db, admin, field, overrides):  # R3.2
    values = {"name": "Valid Name", "ssn": "123-45-6789", "email": "valid@example.test", "dob": date(1990, 1, 1)}
    values.update(overrides)
    before = row(db, "SELECT COUNT(*) AS n FROM `User`")["n"]
    with pytest.raises(ValidationFailed) as exc:
        users.create_user(admin, values["name"], values["ssn"], values["email"], values["dob"])
    assert exc.value.field == field
    assert row(db, "SELECT COUNT(*) AS n FROM `User`")["n"] == before


def test_duplicate_email_is_rejected_whatever_its_case(admin):  # R3.3
    users.create_user(admin, "First", "123-45-6789", "same@example.test", date(1990, 1, 1))
    with pytest.raises(ValidationFailed) as exc:
        users.create_user(admin, "Second", "123-45-6789", "SAME@example.test", date(1990, 1, 1))
    assert exc.value.field == "email"


def test_only_active_administrators_can_create_users(db, admin, customer):  # R2.4
    with pytest.raises(Forbidden):
        users.create_user(customer, "X", "123-45-6789", "x@example.test", date(1990, 1, 1))
    with pytest.raises(Forbidden):
        users.create_user(999999, "X", "123-45-6789", "x@example.test", date(1990, 1, 1))
    row(db, "UPDATE `User` SET is_active = FALSE WHERE userID = %s", (admin,))
    with pytest.raises(Forbidden):
        users.create_user(admin, "X", "123-45-6789", "x@example.test", date(1990, 1, 1))


# --- deactivate (R3.10, R3.11, D7) --------------------------------------------------------------

def test_deactivating_keeps_the_user_and_their_records(db, admin, customer):  # D7
    portfolio_id = make_portfolio(db, customer)
    users.deactivate_user(admin, customer)
    assert row(db, "SELECT is_active FROM `User` WHERE userID = %s", (customer,))["is_active"] == 0
    assert row(db, "SELECT COUNT(*) AS n FROM Portfolio WHERE portfolioID = %s", (portfolio_id,))["n"] == 1
    with pytest.raises(InvalidCredentials):
        users.authenticate(email_of(db, customer), PASSWORD, T0)


def test_an_admin_cannot_deactivate_themselves(admin):
    with pytest.raises(ValidationFailed):
        users.deactivate_user(admin, admin)


def test_deactivating_an_unknown_user_is_not_found(admin):
    with pytest.raises(NotFound):
        users.deactivate_user(admin, 999999)


def test_customers_cannot_deactivate_anyone(admin, customer):
    with pytest.raises(Forbidden):
        users.deactivate_user(customer, admin)


# --- list_users (R3.7, D8) ----------------------------------------------------------------------

def test_user_list_shows_roles_and_never_an_ssn(admin, customer):  # R3.7, D8
    users.create_user(admin, "Sam", "987-65-4321", "sam@example.test", date(1990, 1, 1))
    listed = users.list_users()
    assert {u.email: u.role for u in listed} == {
        "ada@example.test": "Administrator",
        "casey@example.test": "Customer",
        "sam@example.test": "Customer",
    }
    assert "ssn" not in {f.name for f in dataclasses.fields(UserRow)}
    assert "4321" not in repr(listed) and "987-65" not in repr(listed)


def test_admin_rows_carry_access_level_and_department(admin):  # R2.7
    found = users.get_user(admin)
    assert found.is_admin and found.access_level == "superadmin" and found.department == "Operations"


# --- create_admin (bootstrap) ---------------------------------------------------------------------

def test_create_admin_makes_an_administrator(db, app_ctx):
    user_id = users.create_admin("Root Admin", "111-22-3333", "Boss@Example.test", date(1980, 1, 1),
                                 "a-long-password", "superadmin", "")
    found = users.get_user(user_id)
    assert found.is_admin and found.email == "boss@example.test" and found.department is None
    assert users.authenticate("boss@example.test", "a-long-password", T0).user_id == user_id


def test_create_admin_requires_a_long_password(app_ctx):
    with pytest.raises(ValidationFailed) as exc:
        users.create_admin("A", "111-22-3333", "a@example.test", date(1980, 1, 1), "short", "admin", None)
    assert exc.value.field == "password"


# --- Roles (R3.1, R3.9, D19) ---------------------------------------------------------------------

def test_admin_creates_an_administrator(db, admin):  # R3.9, D19
    user_id, temp = users.create_user(admin, "Nora Admin", "123-45-6789", "nora@example.test", date(1985, 2, 2),
                                      role="Administrator", access_level=" operations ", department="")
    found = users.get_user(user_id)
    assert found.is_admin and found.role == "Administrator"
    assert found.access_level == "operations" and found.department is None
    assert found.must_change_password  # administrators start on a temporary password too (R18.1)
    assert check_password_hash(row(db, "SELECT password_hash FROM `User` WHERE userID = %s", (user_id,))["password_hash"], temp)


def test_customers_are_the_default_role(admin):
    user_id, _ = users.create_user(admin, "Cal", "123-45-6789", "cal@example.test", date(1990, 1, 1))
    assert users.get_user(user_id).role == "Customer"


@pytest.mark.parametrize("role, access_level, department, field", [
    ("Administrator", None, None, "access_level"),
    ("Administrator", "x" * 21, None, "access_level"),
    ("Administrator", "admin", "x" * 101, "department"),
    ("Superuser", None, None, "role"),
    ("", None, None, "role"),
])
def test_invalid_role_details_are_rejected_and_nothing_is_created(db, admin, role, access_level, department, field):
    before = row(db, "SELECT COUNT(*) AS n FROM `User`")["n"]
    with pytest.raises(ValidationFailed) as exc:
        users.create_user(admin, "X", "123-45-6789", "x@example.test", date(1990, 1, 1),
                          role=role, access_level=access_level, department=department)
    assert exc.value.field == field
    assert row(db, "SELECT COUNT(*) AS n FROM `User`")["n"] == before


def test_customer_only_actions_refuse_administrators(admin):  # D19
    from app.services import bank_accounts, portfolios

    with pytest.raises(Forbidden):
        portfolios.create_portfolio(admin, "Mine", "Brokerage")
    with pytest.raises(Forbidden):
        bank_accounts.add_bank_account(admin, "Bank", "12345678", "021000021", "Main")


# --- Editing users (R3.12, D20) -------------------------------------------------------------------

def test_admin_edits_name_email_and_date_of_birth(admin, customer):  # R3.12
    edited = users.update_user(admin, customer, "  Casey Renamed ", "New@Example.test", date(1991, 7, 7))
    assert (edited.name, edited.email, edited.date_of_birth) == ("Casey Renamed", "new@example.test", date(1991, 7, 7))
    assert edited.role == "Customer"  # the role can't be changed here


def test_editing_never_touches_the_ssn(db, admin, customer):  # D20
    before = row(db, "SELECT ssn FROM `User` WHERE userID = %s", (customer,))["ssn"]
    users.update_user(admin, customer, "Casey", "casey@example.test", date(1990, 1, 1))
    assert row(db, "SELECT ssn FROM `User` WHERE userID = %s", (customer,))["ssn"] == before


@pytest.mark.parametrize("field, name, email, dob", [
    ("name", "", "casey@example.test", date(1990, 1, 1)),
    ("email", "Casey", "not-an-email", date(1990, 1, 1)),
    ("email", "Casey", "ada@example.test", date(1990, 1, 1)),   # already used by the admin
    ("date_of_birth", "Casey", "casey@example.test", date.today() + timedelta(days=1)),
])
def test_invalid_edits_are_rejected(db, admin, customer, field, name, email, dob):
    with pytest.raises(ValidationFailed) as exc:
        users.update_user(admin, customer, name, email, dob)
    assert exc.value.field == field
    assert users.get_user(customer).name == "Casey Customer"


def test_editing_needs_an_admin_and_an_existing_user(admin, customer):
    with pytest.raises(Forbidden):
        users.update_user(customer, admin, "X", "x@example.test", date(1990, 1, 1))
    with pytest.raises(NotFound):
        users.update_user(admin, 999999, "X", "x@example.test", date(1990, 1, 1))
