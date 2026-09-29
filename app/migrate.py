"""Schema migrations and the app account's grants (design: DD2, Database accounts).

Migrations are numbered .sql files in migrations/ (e.g. 001_initial_schema.sql),
applied in order by the trading_migrate account and recorded in
schema_migrations. When plain SQL can't do the job, a migration may instead be a
.py file with the same kind of name that defines upgrade(cursor); .sql and .py
migrations share one numbering and run in number order. After migrating, trading_app's table-level privileges are
(re)applied from APP_PRIVILEGES; MySQL only allows these grants on tables that
exist, which is why they cannot be set up earlier.

MySQL commits DDL immediately, so a migration that fails part-way leaves the
statements before the failure applied. Keep each migration small, and fix
forward with a new migration rather than editing an applied one.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.(?:sql|py)$")
MIGRATION_SUFFIXES = (".sql", ".py")
IDENTIFIER = re.compile(r"^[A-Za-z0-9_]{1,64}$")

# trading_app privileges per table (design: Database accounts; N3.6, R17.2).
# No DELETE anywhere except Inventory (positions are deleted at zero), and no
# UPDATE on Transaction or History, so the audit trail cannot be changed through the app.
APP_PRIVILEGES: dict[str, str] = {
    "User": "SELECT, INSERT, UPDATE",
    "Admin": "SELECT, INSERT, UPDATE",
    "BankAccount": "SELECT, INSERT, UPDATE",
    "Portfolio": "SELECT, INSERT, UPDATE",
    "Issuer": "SELECT, INSERT, UPDATE",
    "StockPrice": "SELECT, INSERT, UPDATE",
    "Inventory": "SELECT, INSERT, UPDATE, DELETE",
    "Transaction": "SELECT, INSERT",
    "Transfer": "SELECT, INSERT, UPDATE",
    "History": "SELECT, INSERT",
    "MarketSchedule": "SELECT, INSERT, UPDATE",
    "SystemState": "SELECT, INSERT, UPDATE",
    "PortfolioValueDaily": "SELECT, INSERT, UPDATE",
}


def _identifier(name: str) -> str:
    if not IDENTIFIER.fullmatch(name or ""):
        raise ValueError(f"unsafe or empty database/account name: {name!r}")
    return name


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Migration files in order. Fails on badly named or duplicate-numbered files."""
    files = sorted((p for p in directory.iterdir() if p.is_file() and p.suffix in MIGRATION_SUFFIXES),
                   key=lambda p: p.name)
    seen: dict[str, str] = {}
    for path in files:
        match = MIGRATION_NAME.match(path.name)
        if not match:
            raise ValueError(f"migration file name must look like 001_description.sql or .py: {path.name}")
        number = match.group(1)
        if number in seen:
            raise ValueError(f"two migrations share number {number}: {seen[number]} and {path.name}")
        seen[number] = path.name
    return files


def split_sql(text: str) -> list[str]:
    """Split a migration into statements on ';', ignoring comments and semicolons inside quotes."""
    statements: list[str] = []
    current: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        ch = text[i]
        if quote:
            current.append(ch)
            if ch == "\\" and quote != "`" and i + 1 < n:
                current.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
            current.append(ch)
        elif text.startswith("--", i) and (i + 2 == n or text[i + 2] in " \t\r\n"):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end == -1:
                raise ValueError("unterminated /* comment in migration")
            i = end + 2
            continue
        elif ch == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(ch)
        i += 1
    if quote:
        raise ValueError("unterminated quoted string in migration")
    if "".join(current).strip():
        raise ValueError("migration ends with a statement that has no closing ';'")
    return statements


def ensure_migrations_table(cursor) -> None:
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " filename VARCHAR(255) PRIMARY KEY,"
        " applied_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3))"
    )


def applied_migrations(cursor) -> set[str]:
    ensure_migrations_table(cursor)
    cursor.execute("SELECT filename FROM schema_migrations")
    return {row["filename"] if isinstance(row, dict) else row[0] for row in cursor.fetchall()}


def upgrade(conn, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every migration not yet recorded. Returns the file names applied now."""
    applied_now: list[str] = []
    with conn.cursor() as cursor:
        done = applied_migrations(cursor)
        conn.commit()
        for path in list_migrations(directory):
            if path.name in done:
                continue
            if path.suffix == ".py":
                run_python_migration(path, cursor)
            else:
                for statement in split_sql(path.read_text(encoding="utf-8")):
                    cursor.execute(statement)  # sql-safe: a statement from a migration file in the repository, not user input
            cursor.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            conn.commit()
            applied_now.append(path.name)
    return applied_now


def run_python_migration(path: Path, cursor) -> None:
    """Load a .py migration and call its upgrade(cursor)."""
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    upgrade_function = getattr(module, "upgrade", None)
    if not callable(upgrade_function):
        raise ValueError(f"Python migration {path.name} must define upgrade(cursor)")
    upgrade_function(cursor)


def grant_app_privileges(conn, database: str, app_user: str, host: str = "localhost") -> None:
    """Give the app account exactly APP_PRIVILEGES on each table (run after migrating)."""
    database = _identifier(database)
    with conn.cursor() as cursor:
        for table, privileges in APP_PRIVILEGES.items():
            # sql-safe: privileges come from APP_PRIVILEGES and names pass _identifier(); GRANT takes no parameters for them
            cursor.execute(f"GRANT {privileges} ON `{database}`.`{_identifier(table)}` TO %s@%s", (app_user, host))
    conn.commit()
