"""Local database setup script (task 0.2): the parts that can be tested without a MySQL admin login."""
import importlib.util
import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.config import REQUIRED

REPO_ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("setup_local_db", REPO_ROOT / "scripts" / "setup_local_db.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

EXAMPLE_TEXT = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


def test_blank_secrets_are_filled_and_reported():
    text, filled = setup.fill_blank_secrets(EXAMPLE_TEXT)
    env = setup.read_env(text)
    assert sorted(filled) == sorted(setup.GENERATORS)
    for key in setup.GENERATORS:
        assert env[key], f"{key} was not filled"


def test_existing_values_are_never_overwritten():
    text = "SECRET_KEY=keep-me\nDB_PASSWORD=\nDB_NAME=trading_db\n"
    new_text, filled = setup.fill_blank_secrets(text)
    env = setup.read_env(new_text)
    assert env["SECRET_KEY"] == "keep-me"
    assert env["DB_NAME"] == "trading_db"
    assert filled == ["DB_PASSWORD"]


def test_comments_and_line_endings_are_preserved():
    text = "# comment\r\nSECRET_KEY=\r\nAPP_ENV=development\r\n"
    new_text, _ = setup.fill_blank_secrets(text)
    assert new_text.startswith("# comment\r\nSECRET_KEY=")
    assert new_text.endswith("\r\nAPP_ENV=development\r\n")


def test_generated_encryption_key_is_a_valid_fernet_key():  # N1.3, DD8
    key = setup.GENERATORS["FIELD_ENCRYPTION_KEYS"]()
    Fernet(key.encode())  # raises if the key is malformed


def test_generated_db_passwords_are_strong_and_unique():
    passwords = {setup.generate_db_password() for _ in range(50)}
    assert len(passwords) == 50
    for pw in passwords:
        assert len(pw) >= 20
        assert re.search(r"[a-z]", pw) and re.search(r"[A-Z]", pw)
        assert re.search(r"\d", pw) and re.search(r"[^A-Za-z0-9]", pw)


@pytest.mark.parametrize("name", ["", "trading db", "trading`db", "db;DROP", "a" * 65, "tr-ading", "db'"])
def test_unsafe_names_are_rejected_before_reaching_sql(name):  # N2.1
    with pytest.raises(ValueError):
        setup.check_identifier(name)


def test_setup_sql_passes_account_names_and_passwords_as_parameters():  # N2.1
    env = setup.read_env(setup.fill_blank_secrets(EXAMPLE_TEXT)[0])
    statements = setup.setup_statements(env)
    all_sql = " ".join(sql for sql, _ in statements)
    assert env["DB_PASSWORD"] not in all_sql
    assert env["DB_MIGRATE_PASSWORD"] not in all_sql
    assert "trading_app" not in all_sql and "trading_migrate" not in all_sql
    assert "CREATE DATABASE IF NOT EXISTS `trading_db`" in all_sql
    assert "CREATE DATABASE IF NOT EXISTS `trading_test`" in all_sql
    assert all_sql.count("utf8mb4_0900_ai_ci") == 2
    # trading_app gets no privileges here; db-upgrade grants its table privileges after migrating.
    grants = [(sql, params) for sql, params in statements if sql.startswith("GRANT")]
    assert len(grants) == 2 and all(params == ("trading_migrate",) for _, params in grants)
    # trading_migrate can pass on privileges only within the two project databases
    assert all(sql.endswith("WITH GRANT OPTION") and "`.*" in sql and "*.*" not in sql for sql, _ in grants)


def test_admin_username_and_password_are_asked_for():
    asked = []
    user, password = setup.prompt_admin_credentials(
        None,
        ask=lambda prompt: asked.append(prompt) or "  thor  ",
        ask_secret=lambda prompt: asked.append(prompt) or "s3cret",
    )
    assert (user, password) == ("thor", "s3cret")
    assert asked == ["MySQL admin username: ", "MySQL password for 'thor' (not stored): "]


def test_admin_username_given_on_command_line_is_not_asked_again():
    def fail(prompt):
        raise AssertionError("username should not be asked for")

    user, _ = setup.prompt_admin_credentials("thor", ask=fail, ask_secret=lambda prompt: "pw")
    assert user == "thor"


def test_blank_admin_username_stops_the_script():
    with pytest.raises(SystemExit, match="username is required"):
        setup.prompt_admin_credentials(None, ask=lambda prompt: "   ", ask_secret=lambda prompt: "pw")


@pytest.mark.parametrize(
    "text, expected",
    [("8.0.46", (8, 0, 46)), ("8.0.19-log", (8, 0, 19)), ("8.4.3 MySQL Community Server", (8, 4, 3))],
)
def test_mysql_version_parsing(text, expected):
    assert setup.parse_version(text) == expected
    assert (setup.parse_version(text) >= setup.MIN_MYSQL_VERSION) is True


def test_env_example_lists_every_required_setting_with_no_secret_values():  # N1.4
    env = setup.read_env(EXAMPLE_TEXT)
    for key in REQUIRED:
        assert key in env, f".env.example is missing {key}"
    for key in setup.GENERATORS:
        assert env[key] == "", f".env.example must not contain a value for {key}"
