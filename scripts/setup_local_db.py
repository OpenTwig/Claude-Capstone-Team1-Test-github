"""Set up local development settings and MySQL databases (task 0.2).

Run once from the repo root, inside the venv:

    .venv\\Scripts\\python scripts\\setup_local_db.py          # full setup (asks for your MySQL username and password)
    .venv\\Scripts\\python scripts\\setup_local_db.py --check  # verify only; uses .env, no admin login

Full setup:
1. Creates .env from .env.example if it does not exist, and fills in any blank
   SECRET_KEY, FIELD_ENCRYPTION_KEYS, DB_PASSWORD, and DB_MIGRATE_PASSWORD with
   generated values. Existing values are never overwritten.
2. Asks for your MySQL admin username and password (typed at the prompt, never
   stored), then creates the app and test databases and the trading_migrate and
   trading_app accounts. Safe to re-run: it creates what is missing and resets
   the two account passwords to match .env.
3. Runs the same checks as --check.

The admin account does not have to be root, but it needs these privileges:
CREATE (databases), CREATE USER, and ALL PRIVILEGES WITH GRANT OPTION on the
two databases (so it can grant them to trading_migrate).

trading_app gets no table privileges here: MySQL only grants table-level
privileges on tables that exist, so `flask db-upgrade` applies them after
migrating (trading_migrate has GRANT OPTION on the two databases for this).
"""
from __future__ import annotations

import argparse
import getpass
import io
import re
import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
EXAMPLE_PATH = REPO_ROOT / ".env.example"

MIN_MYSQL_VERSION = (8, 0, 19)
IDENTIFIER = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def generate_db_password() -> str:
    """A random password that also satisfies MySQL's validate_password MEDIUM policy."""
    return f"{secrets.token_urlsafe(24)}-Aa1"


GENERATORS = {
    "SECRET_KEY": lambda: secrets.token_urlsafe(48),
    "FIELD_ENCRYPTION_KEYS": lambda: Fernet.generate_key().decode(),
    "DB_PASSWORD": generate_db_password,
    "DB_MIGRATE_PASSWORD": generate_db_password,
}


def read_env(text: str) -> dict[str, str]:
    return {k: (v or "") for k, v in dotenv_values(stream=io.StringIO(text)).items()}


def fill_blank_secrets(text: str, generators=GENERATORS) -> tuple[str, list[str]]:
    """Fill `KEY=` lines that are blank with generated values; never overwrite a set value.

    Returns the new text and the keys that were filled.
    """
    filled: list[str] = []
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        newline = line[len(stripped):]
        match = re.fullmatch(r"([A-Z_]+)=\s*", stripped)
        if match and match.group(1) in generators:
            key = match.group(1)
            out.append(f"{key}={generators[key]()}{newline}")
            filled.append(key)
        else:
            out.append(line)
    return "".join(out), filled


def check_identifier(name: str) -> str:
    """Database and account names are interpolated into SQL, so allow only safe characters."""
    if not IDENTIFIER.fullmatch(name or ""):
        raise ValueError(f"unsafe or empty database/account name: {name!r}")
    return name


def setup_statements(env: dict[str, str]) -> list[tuple[str, tuple]]:
    """SQL to create the databases and accounts. Names are validated; values are parameters."""
    db_name = check_identifier(env["DB_NAME"])
    test_db = check_identifier(env.get("DB_TEST_NAME") or "trading_test")
    app_user = check_identifier(env["DB_USER"])
    migrate_user = check_identifier(env["DB_MIGRATE_USER"])

    statements: list[tuple[str, tuple]] = []
    for db in (db_name, test_db):
        statements.append(
            (f"CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci", ())
        )
    for user, password in ((migrate_user, env["DB_MIGRATE_PASSWORD"]), (app_user, env["DB_PASSWORD"])):
        statements.append(("CREATE USER IF NOT EXISTS %s@'localhost' IDENTIFIED BY %s", (user, password)))
        statements.append(("ALTER USER %s@'localhost' IDENTIFIED BY %s", (user, password)))
    for db in (db_name, test_db):
        # GRANT OPTION lets `flask db-upgrade` apply trading_app's table grants after migrating.
        statements.append((f"GRANT ALL PRIVILEGES ON `{db}`.* TO %s@'localhost' WITH GRANT OPTION", (migrate_user,)))
    return statements


def parse_version(text: str) -> tuple[int, int, int]:
    numbers = re.match(r"(\d+)\.(\d+)\.(\d+)", text)
    if not numbers:
        raise ValueError(f"cannot read MySQL version from {text!r}")
    return tuple(int(n) for n in numbers.groups())  # type: ignore[return-value]


def ensure_env_file() -> dict[str, str]:
    created = not ENV_PATH.exists()
    text = ENV_PATH.read_text(encoding="utf-8") if not created else EXAMPLE_PATH.read_text(encoding="utf-8")
    new_text, filled = fill_blank_secrets(text)
    if created or filled:
        ENV_PATH.write_text(new_text, encoding="utf-8")
    print(f".env {'created from .env.example' if created else 'found'}"
          + (f"; generated {', '.join(filled)}" if filled else "; no blank secrets to fill"))
    return read_env(new_text)


def connect(env: dict[str, str], user: str, password: str, database: str | None = None):
    import pymysql

    return pymysql.connect(
        host=env.get("DB_HOST") or "localhost",
        port=int(env.get("DB_PORT") or 3306),
        user=user,
        password=password,
        database=database,
        connect_timeout=10,
    )


PRIVILEGE_HELP = (
    "Your MySQL account needs CREATE (databases), CREATE USER, and ALL PRIVILEGES "
    "WITH GRANT OPTION on the app and test databases. Ask whoever manages the "
    "server, or use an account that has them."
)


def prompt_admin_credentials(admin_user: str | None, ask=input, ask_secret=getpass.getpass) -> tuple[str, str]:
    """Ask for the MySQL admin username (unless given) and password. Nothing is stored."""
    user = (admin_user or ask("MySQL admin username: ")).strip()
    if not user:
        raise SystemExit("A MySQL admin username is required.")
    password = ask_secret(f"MySQL password for {user!r} (not stored): ")
    return user, password


def run_setup(env: dict[str, str], admin_user: str, admin_password: str) -> None:
    import pymysql

    try:
        conn = connect(env, admin_user, admin_password)
    except pymysql.err.OperationalError as exc:
        raise SystemExit(f"Could not log in to MySQL as {admin_user!r}: {exc}")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version_text = cur.fetchone()[0]
            if parse_version(version_text) < MIN_MYSQL_VERSION:
                raise SystemExit(f"MySQL {version_text} is too old; 8.0.19 or later is required")
            print(f"MySQL {version_text}, logged in as {admin_user!r}")
            for sql, params in setup_statements(env):
                try:
                    cur.execute(sql, params)
                except (pymysql.err.OperationalError, pymysql.err.ProgrammingError) as exc:
                    raise SystemExit(f"MySQL refused a setup step: {exc}\n{PRIVILEGE_HELP}")
        conn.commit()
    finally:
        conn.close()
    print("Databases and accounts created or updated.")


def run_checks(env: dict[str, str]) -> bool:
    """Connect with the .env credentials: migrate account to both databases, app account to the server."""
    test_db = env.get("DB_TEST_NAME") or "trading_test"
    checks = [
        (f"{env['DB_MIGRATE_USER']} -> {env['DB_NAME']}", env["DB_MIGRATE_USER"], env["DB_MIGRATE_PASSWORD"], env["DB_NAME"]),
        (f"{env['DB_MIGRATE_USER']} -> {test_db}", env["DB_MIGRATE_USER"], env["DB_MIGRATE_PASSWORD"], test_db),
        (f"{env['DB_USER']} -> server (table grants come after migrations)", env["DB_USER"], env["DB_PASSWORD"], None),
    ]
    ok = True
    for label, user, password, database in checks:
        try:
            conn = connect(env, user, password, database)
            with conn.cursor() as cur:
                cur.execute("SELECT CURRENT_USER(), DATABASE(), VERSION()")
                current, db, version = cur.fetchone()
            conn.close()
            print(f"  OK    {label}  (as {current}, database {db}, MySQL {version})")
        except Exception as exc:  # report every failure, then exit non-zero
            ok = False
            print(f"  FAIL  {label}: {exc}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only verify connections using .env")
    parser.add_argument("--admin-user", help="MySQL admin username (asked for if not given)")
    args = parser.parse_args(argv)

    if args.check:
        if not ENV_PATH.exists():
            print(".env not found; run without --check first")
            return 1
        env = read_env(ENV_PATH.read_text(encoding="utf-8"))
    else:
        env = ensure_env_file()
        admin_user, admin_password = prompt_admin_credentials(args.admin_user)
        run_setup(env, admin_user, admin_password)

    print("Checking connections:")
    return 0 if run_checks(env) else 1


if __name__ == "__main__":
    sys.exit(main())
