"""Users: login, lockout, administrator-managed accounts, and first-login password changes
(design: Authentication and sessions, User administration; R1, R3, R18, D2, D7, D8, D10, A1, A18).
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from functools import lru_cache

import pymysql
from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from app import clock
from app.crypto import encrypt_field
from app.db import get_db, transaction
from app.errors import (
    AccountLocked,
    Forbidden,
    InvalidCredentials,
    NotFound,
    TemporaryPasswordExpired,
    ValidationFailed,
)
from app.services import validation
from app.services.paging import PER_PAGE, ListPage, clamp
from app.services.types import UserRow

DUPLICATE_KEY = 1062
ROLES = ("Customer", "Administrator")  # D19

_USER_COLUMNS = (
    "u.userID, u.name, u.email, u.date_of_birth, u.is_active, u.signup,"
    " a.adminID IS NOT NULL AS is_admin, a.access_level, a.department,"
    " u.must_change_password, u.session_version"
)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Checked against when the email is unknown, so response time doesn't reveal which emails exist."""
    return generate_password_hash(secrets.token_urlsafe(16))


def _row(r: dict) -> UserRow:
    return UserRow(
        user_id=r["userID"],
        name=r["name"],
        email=r["email"],
        date_of_birth=r["date_of_birth"],
        is_active=bool(r["is_active"]),
        signup=r["signup"],
        is_admin=bool(r["is_admin"]),
        access_level=r["access_level"],
        department=r["department"],
        must_change_password=bool(r["must_change_password"]),
        session_version=r["session_version"],
    )


def get_user(user_id: int) -> UserRow | None:
    with get_db().cursor() as cur:
        cur.execute(
            f"SELECT {_USER_COLUMNS} FROM `User` u LEFT JOIN Admin a ON a.userID = u.userID WHERE u.userID = %s",
            (user_id,),
        )
        found = cur.fetchone()
    get_db().commit()  # end the read so the next request sees fresh data
    return _row(found) if found else None


def _naive_utc(at_utc: datetime) -> datetime:
    """DATETIME(3) columns hold UTC without a zone; compare like with like."""
    return at_utc.replace(tzinfo=None)


def authenticate(email: str, password: str, at_utc: datetime | None = None) -> UserRow:
    """Check an email and password, applying the lockout rule (A1).

    Unknown emails, wrong passwords, and deactivated users all raise the same
    InvalidCredentials (R1.4, R3.11). A locked account raises AccountLocked
    until the lock expires, even with the right password (R1.8, R18.11). A
    correct temporary password past its expiry raises TemporaryPasswordExpired;
    only someone who knows the password learns that (R18.8).
    """
    settings = current_app.config["SETTINGS"]
    now = _naive_utc(at_utc or clock.now_utc())
    normalized = (email or "").strip().lower()

    with transaction() as cur:
        cur.execute(
            "SELECT userID, password_hash, is_active, failed_login_count, locked_until,"
            " must_change_password, temp_password_expires_at"
            " FROM `User` WHERE email = %s FOR UPDATE",
            (normalized,),
        )
        user = cur.fetchone()
        if user is None:
            check_password_hash(_dummy_hash(), password or "")
            raise InvalidCredentials()

        if user["locked_until"] is not None and user["locked_until"] > now:
            raise AccountLocked(settings.login_lock_minutes)

        if check_password_hash(user["password_hash"], password or "") and user["is_active"]:
            expires = user["temp_password_expires_at"]
            if user["must_change_password"] and expires is not None and expires <= now:
                raise TemporaryPasswordExpired()
            cur.execute(
                "UPDATE `User` SET failed_login_count = 0, locked_until = NULL WHERE userID = %s",
                (user["userID"],),
            )
            user_id = user["userID"]
        else:
            failures = user["failed_login_count"] + 1
            if failures >= settings.login_max_failures:
                cur.execute(
                    "UPDATE `User` SET failed_login_count = 0, locked_until = %s WHERE userID = %s",
                    (now + timedelta(minutes=settings.login_lock_minutes), user["userID"]),
                )
            else:
                cur.execute("UPDATE `User` SET failed_login_count = %s WHERE userID = %s", (failures, user["userID"]))
            user_id = None

    if user_id is None:
        raise InvalidCredentials()
    return get_user(user_id)


def require_admin(admin_user_id: int) -> None:
    caller = get_user(admin_user_id)
    if caller is None or not caller.is_admin or not caller.is_active:
        raise Forbidden()


def require_customer(user_id: int) -> None:
    """Administrators manage the system and don't trade (D19): refuse customer-only actions."""
    user = get_user(user_id)
    if user is None or user.is_admin:
        raise Forbidden()


def _temporary_password_expiry() -> datetime:
    """When a temporary password issued now stops working (A18)."""
    days = current_app.config["SETTINGS"].temp_password_days
    return _naive_utc(clock.now_utc()) + timedelta(days=days)


def _insert_user(cur, name: str, ssn: str, email: str, dob: date, password: str, temporary: bool) -> int:
    try:
        cur.execute(
            "INSERT INTO `User` (name, date_of_birth, ssn, email, password_hash, must_change_password,"
            " temp_password_expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (name, dob, encrypt_field(ssn), email, generate_password_hash(password), temporary,
             _temporary_password_expiry() if temporary else None),
        )
    except pymysql.err.IntegrityError as exc:
        if exc.args[0] == DUPLICATE_KEY:
            raise ValidationFailed("email", "A user with this email already exists.") from exc
        raise
    return cur.lastrowid


def _validated_person(name, ssn, email, dob) -> tuple[str, str, str, date]:
    today = clock.now_local().date()
    return (
        validation.text("name", name, "Full name", 255),
        validation.ssn(ssn),
        validation.email(email),
        validation.date_of_birth(dob, today),
    )


def _validated_admin_details(access_level, department) -> tuple[str, str | None]:
    return (
        validation.text("access_level", access_level, "Access level", 20),
        validation.text("department", department, "Department", 100, required=False),
    )


def create_user(
    admin_user_id: int,
    name: str,
    ssn: str,
    email: str,
    dob: date,
    role: str = "Customer",
    access_level: str | None = None,
    department: str | None = None,
) -> tuple[int, str]:
    """Create a customer or administrator account (R3.1-R3.6, R3.9, D2, D19).

    Returns (user_id, temporary_password). The temporary password is returned once for
    the administrator to hand over; only its hash is stored (R3.5). The SSN is stored
    encrypted (R3.6). The user must replace the password at first login, within 7 days
    (R18.1). Administrators also need an access level; the department is optional.
    """
    require_admin(admin_user_id)
    if role not in ROLES:
        raise ValidationFailed("role", "Choose Customer or Administrator.")
    name, ssn, email, dob = _validated_person(name, ssn, email, dob)
    if role == "Administrator":
        access_level, department = _validated_admin_details(access_level, department)
    temporary_password = secrets.token_urlsafe(12)
    with transaction() as cur:
        user_id = _insert_user(cur, name, ssn, email, dob, temporary_password, temporary=True)
        if role == "Administrator":
            cur.execute("INSERT INTO Admin (userID, access_level, department) VALUES (%s, %s, %s)",
                        (user_id, access_level, department))
    return user_id, temporary_password


def update_user(admin_user_id: int, user_id: int, name: str, email: str, dob: date) -> UserRow:
    """Edit a user's name, email, and date of birth (R3.12, D20). The SSN and role can't be changed."""
    require_admin(admin_user_id)
    if get_user(user_id) is None:
        raise NotFound()
    name = validation.text("name", name, "Full name", 255)
    email = validation.email(email)
    dob = validation.date_of_birth(dob, clock.now_local().date())
    try:
        with transaction() as cur:
            cur.execute("UPDATE `User` SET name = %s, email = %s, date_of_birth = %s WHERE userID = %s",
                        (name, email, dob, user_id))
    except pymysql.err.IntegrityError as exc:
        if exc.args[0] == DUPLICATE_KEY:
            raise ValidationFailed("email", "A user with this email already exists.") from exc
        raise
    return get_user(user_id)


MIN_ADMIN_PASSWORD_LENGTH = validation.PASSWORD_MIN


def create_admin(
    name: str, ssn: str, email: str, dob: date, password: str, access_level: str, department: str | None
) -> int:
    """Create an administrator (used by `flask create-admin` to bootstrap the first one)."""
    name, ssn, email, dob = _validated_person(name, ssn, email, dob)
    password = validation.password(password, email)  # N1.6: the same rule any future password form uses
    access_level, department = _validated_admin_details(access_level, department)
    with transaction() as cur:
        user_id = _insert_user(cur, name, ssn, email, dob, password, temporary=False)
        cur.execute(
            "INSERT INTO Admin (userID, access_level, department) VALUES (%s, %s, %s)",
            (user_id, access_level, department),
        )
    return user_id


def deactivate_user(admin_user_id: int, user_id: int) -> None:
    """'Delete' a user: they can no longer log in, and their records are kept (R3.10, D7)."""
    require_admin(admin_user_id)
    if admin_user_id == user_id:
        raise ValidationFailed("user", "You can't deactivate your own account.")
    with transaction() as cur:
        cur.execute("UPDATE `User` SET is_active = FALSE WHERE userID = %s", (user_id,))
        if cur.rowcount == 0:
            cur.execute("SELECT 1 FROM `User` WHERE userID = %s", (user_id,))
            if cur.fetchone() is None:
                raise NotFound()


def change_password(user_id: int, new_password: str, confirm: str) -> UserRow:
    """Replace a temporary password at first login (R18.5-R18.7, D10).

    Only allowed while a change is required: there is no other way to change a
    password. Ends the user's other logins by bumping session_version.
    """
    with transaction() as cur:
        cur.execute("SELECT email, password_hash, must_change_password FROM `User` WHERE userID = %s FOR UPDATE",
                    (user_id,))
        user = cur.fetchone()
        if user is None:
            raise NotFound()
        if not user["must_change_password"]:
            raise Forbidden()
        if (new_password or "") != (confirm or ""):
            raise ValidationFailed("confirm_password", "The two passwords don't match.")
        new_password = validation.password(new_password, user["email"], field="new_password")
        if check_password_hash(user["password_hash"], new_password):
            raise ValidationFailed("new_password", "Choose a password different from your temporary one.")
        cur.execute(
            "UPDATE `User` SET password_hash = %s, must_change_password = FALSE, temp_password_expires_at = NULL,"
            " session_version = session_version + 1 WHERE userID = %s",
            (generate_password_hash(new_password), user_id),
        )
    return get_user(user_id)


def _issue_temporary_password(cur, user_id: int) -> str:
    temporary_password = secrets.token_urlsafe(12)
    cur.execute(
        "UPDATE `User` SET password_hash = %s, must_change_password = TRUE, temp_password_expires_at = %s,"
        " failed_login_count = 0, locked_until = NULL, session_version = session_version + 1 WHERE userID = %s",
        (generate_password_hash(temporary_password), _temporary_password_expiry(), user_id),
    )
    if cur.rowcount == 0:
        raise NotFound()
    return temporary_password


def issue_temporary_password(admin_user_id: int, user_id: int) -> str:
    """Give another user a new temporary password (R18.9, R18.10). Returns it, to show once.

    Replaces their password, clears any lockout, sends them through the first-login
    change again, and ends their existing logins. This is how forgotten passwords
    are handled without email (D10).
    """
    require_admin(admin_user_id)
    if admin_user_id == user_id:
        raise ValidationFailed("user", "You can't issue a temporary password to your own account.")
    with transaction() as cur:
        return _issue_temporary_password(cur, user_id)


def issue_temporary_password_by_email(email: str) -> str:
    """Server-side version for `flask issue-temp-password` (e.g. the only admin is locked out)."""
    with transaction() as cur:
        cur.execute("SELECT userID FROM `User` WHERE email = %s", ((email or "").strip().lower(),))
        found = cur.fetchone()
        if found is None:
            raise NotFound("No user has that email address.")
        return _issue_temporary_password(cur, found["userID"])


def list_users() -> list[UserRow]:
    """All users for the Administration page (R3.7). Contains no SSN (D8)."""
    with get_db().cursor() as cur:
        cur.execute(f"SELECT {_USER_COLUMNS} FROM `User` u LEFT JOIN Admin a ON a.userID = u.userID ORDER BY u.name, u.userID")
        rows = cur.fetchall()
    get_db().commit()
    return [_row(r) for r in rows]



def list_users_page(page: int = 1, per_page: int = PER_PAGE) -> ListPage:
    """One page of the users (UserRow), by name, for the Administration page (R3.7). No SSN (D8).

    A page past the end shows the last page.
    """
    with get_db().cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM `User`")
        total = cur.fetchone()["n"]
        page, pages = clamp(page, total, per_page)
        cur.execute(
            f"SELECT {_USER_COLUMNS} FROM `User` u LEFT JOIN Admin a ON a.userID = u.userID"
            " ORDER BY u.name, u.userID LIMIT %s OFFSET %s",
            (per_page, (page - 1) * per_page),
        )
        rows = cur.fetchall()
    get_db().commit()
    return ListPage(rows=tuple(_row(r) for r in rows), page=page, pages=pages, total=total, per_page=per_page)
