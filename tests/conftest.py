"""Shared test fixtures (design: Testing strategy; task 1.1)."""
import os
import random
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values
from hypothesis import settings as hypothesis_settings

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hypothesis profiles: select with HYPOTHESIS_PROFILE=<name> (design: Property-based testing conventions).
# deadline=None because database-backed properties have variable latency.
hypothesis_settings.register_profile("default", max_examples=100, deadline=None)
hypothesis_settings.register_profile("quick", max_examples=20, deadline=None)
hypothesis_settings.register_profile("thorough", max_examples=1000, deadline=None)
hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

# A complete, valid set of settings. Tests pass this to create_app() explicitly,
# so they never depend on a developer's .env file or shell environment.
VALID_ENV = {
    "APP_ENV": "test",
    "SECRET_KEY": "test-secret-key-not-for-real-use",
    "DB_HOST": "localhost",
    "DB_PORT": "3306",
    "DB_NAME": "trading_test",
    "DB_USER": "trading_app",
    "DB_PASSWORD": "test-password",
    # Test-only Fernet keys (newest first). Never use these outside tests.
    "FIELD_ENCRYPTION_KEYS": "jW8R5WD_mr2l0okJDUpBGo4J19A1ezkLjh74a5RR8cs=,KsWe2JTNwVIXFf2RUUxxp4OYnmOWYc7O2O_dPDBijo0=",
}
TEST_KEYS = VALID_ENV["FIELD_ENCRYPTION_KEYS"].split(",")
# A key strong enough for APP_ENV=production (task 6.1), for tests that build a production app.
STRONG_SECRET_KEY = "k3v9Q-Zp2Lw8Rt5Ny1Hb7Xc4Md6Fg0Js"  # test-only; 32 characters, 30 different

DB_KEYS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_MIGRATE_USER", "DB_MIGRATE_PASSWORD")


@pytest.fixture
def valid_env() -> dict[str, str]:
    return dict(VALID_ENV)


@pytest.fixture
def fixed_clock():
    """Call with an aware datetime to fix app.clock at that moment; unfrozen after the test."""
    from app import clock

    yield clock.freeze
    clock.unfreeze()


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """The login rate limiter's in-memory counts are shared by every app built in the test run."""
    from app.security import limiter

    try:
        limiter.reset()
    except Exception:  # not initialised yet in this process
        pass


@pytest.fixture
def rng() -> random.Random:
    """A seeded random generator, so randomized code is repeatable."""
    return random.Random(20260928)


# --- Database -----------------------------------------------------------------------------
# Database tests use the real local MySQL test database (never SQLite), because the design
# depends on MySQL behaviour: DECIMAL, CHECK constraints, FOR UPDATE, and privileges.


@pytest.fixture(scope="session")
def test_database():
    """Rebuild the test database from the migrations once per test session.

    Credentials come from the environment or .env (created by scripts/setup_local_db.py).
    Refuses to touch any database whose name does not end in "_test".
    """
    from app.config import load_settings
    from app.db import connect
    from app.migrate import grant_app_privileges, upgrade

    file_values = dotenv_values(REPO_ROOT / ".env") if (REPO_ROOT / ".env").exists() else {}
    values = {k: os.environ.get(k) or file_values.get(k) or "" for k in (*DB_KEYS, "DB_TEST_NAME")}
    missing = [k for k in DB_KEYS if not values[k]]
    if missing:
        pytest.fail(
            f"Database tests need {', '.join(missing)} in .env. Run scripts/setup_local_db.py first "
            "(or deselect database tests with: -m \"not db\")."
        )
    test_db = values["DB_TEST_NAME"] or "trading_test"
    if not re.fullmatch(r"[A-Za-z0-9_]+_test", test_db):
        pytest.fail(f"Refusing to rebuild database {test_db!r}: test database names must end in '_test'.")

    env = dict(VALID_ENV)
    env.update({k: values[k] for k in DB_KEYS})
    env["DB_NAME"] = test_db
    settings = load_settings(env)

    conn = connect(settings, user=settings.db_migrate_user, password=settings.db_migrate_password)
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            cur.execute("SELECT table_name AS t FROM information_schema.tables WHERE table_schema = %s", (test_db,))
            for row in cur.fetchall():
                if not re.fullmatch(r"[A-Za-z0-9_]+", row["t"]):
                    pytest.fail(f"unexpected table name in {test_db}: {row['t']!r}")
                cur.execute(f"DROP TABLE `{row['t']}`")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()
        applied = upgrade(conn)
        grant_app_privileges(conn, test_db, settings.db_user)
    finally:
        conn.close()
    return SimpleNamespace(settings=settings, env=env, applied=applied)


@pytest.fixture
def db(test_database):
    """A trading_migrate connection to the test database, emptied before the test."""
    from app.db import connect
    from app.migrate import APP_PRIVILEGES

    s = test_database.settings
    conn = connect(s, user=s.db_migrate_user, password=s.db_migrate_password)
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in APP_PRIVILEGES:
            cur.execute(f"TRUNCATE TABLE `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def app_db(db, test_database):
    """A connection as the application account (trading_app), to check what the app may do."""
    from app.db import connect

    conn = connect(test_database.settings)
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def db_env(test_database) -> dict[str, str]:
    """Settings for create_app() that point at the real test database."""
    return dict(test_database.env)


@pytest.fixture
def web_app(db, db_env):
    """The real app on the test database."""
    from app import create_app

    return create_app(db_env)


@pytest.fixture
def client(web_app):
    return web_app.test_client()


@pytest.fixture
def app_ctx(web_app):
    """An application context, for calling services directly.

    Don't combine with the test client: requests would reuse this context and leak flask.g
    between them. Tests that also make requests should use `svc` instead.
    """
    with web_app.app_context():
        yield web_app


@pytest.fixture
def svc(web_app):
    """Run one service call in its own short-lived app context: svc(users.create_user, ...)."""
    def call(func, *args, **kwargs):
        with web_app.app_context():
            return func(*args, **kwargs)
    return call


# --- P17: no response ever shows an SSN or a full bank account number (task 5.3; N1.3, D8) --------------
# Every value the app encrypts during a test (only SSNs and bank account numbers are encrypted) is
# recorded, and every response any test client receives is checked for it, in the whole suite.

_SSN_TEXT = re.compile(r"[0-9]{3}-[0-9]{2}-[0-9]{4}")
_ACCOUNT_TEXT = re.compile(r"[0-9]{6,17}")  # shorter numbers are shown only as "••••" anyway


def sensitive_forms(value: str) -> list[str]:
    """The ways a sensitive value could appear on a page."""
    if _SSN_TEXT.fullmatch(value):
        digits = value.replace("-", "")
        return [value, digits, value.replace("-", " "), value[4:]]   # full, no dashes, spaced, last six ("45-6789")
    if _ACCOUNT_TEXT.fullmatch(value):
        return [value]
    return []


@pytest.fixture(autouse=True)
def no_sensitive_data_in_responses(monkeypatch):
    from flask.testing import FlaskClient

    from app.crypto import FieldCipher

    encrypted: set[str] = set()
    real_encrypt, real_open = FieldCipher.encrypt, FlaskClient.open

    def recording_encrypt(self, plaintext):
        encrypted.add(plaintext)
        return real_encrypt(self, plaintext)

    def checking_open(self, *args, **kwargs):
        response = real_open(self, *args, **kwargs)
        body = response.get_data().decode("utf-8", errors="ignore")
        for value in encrypted:
            for form in sensitive_forms(value):
                assert form not in body, f"P17: a response contains sensitive data ({form[:3]}…)"
        return response

    monkeypatch.setattr(FieldCipher, "encrypt", recording_encrypt)
    monkeypatch.setattr(FlaskClient, "open", checking_open)
    yield encrypted
