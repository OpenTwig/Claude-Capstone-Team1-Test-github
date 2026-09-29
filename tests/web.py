"""Helpers for driving the app through its real pages and forms, CSRF tokens included."""
from __future__ import annotations

import re

from werkzeug.security import generate_password_hash

from tests.factories import make_admin, make_user

CSRF_FIELD = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')

# Fast hashing for test users only (the app itself uses Werkzeug's default, scrypt).
FAST_HASH = "pbkdf2:sha256:1000"


def csrf_from(html: str) -> str:
    match = CSRF_FIELD.search(html)
    assert match, "no CSRF token field in page"
    return match.group(1)


def csrf_token(client, path: str = "/signin") -> str:
    return csrf_from(client.get(path).get_data(as_text=True))


def login(client, email: str, password: str, stay_signed_in: bool = False, **post_kwargs):
    data = {"email": email, "password": password, "csrf_token": csrf_token(client)}
    if stay_signed_in:
        data["stay_signed_in"] = "y"
    return client.post("/signin", data=data, **post_kwargs)


def logout(client, from_path: str = "/account"):
    return client.post("/logout", data={"csrf_token": csrf_token(client, from_path)})


def make_login_user(db, password: str = "correct horse battery", *, admin: bool = False, **kwargs) -> int:
    """A user who can actually log in. Returns the userID."""
    user_id = make_user(db, password_hash=generate_password_hash(password, method=FAST_HASH), **kwargs)
    if admin:
        make_admin(db, user_id=user_id, access_level="superadmin", department="Operations")
    return user_id


def email_of(db, user_id: int) -> str:
    with db.cursor() as cur:
        cur.execute("SELECT email FROM `User` WHERE userID = %s", (user_id,))
        email = cur.fetchone()["email"]
    db.commit()
    return email


def set_cookies(response) -> dict[str, str]:
    """Set-Cookie headers by cookie name."""
    cookies = {}
    for header in response.headers.getlist("Set-Cookie"):
        cookies[header.split("=", 1)[0]] = header
    return cookies
