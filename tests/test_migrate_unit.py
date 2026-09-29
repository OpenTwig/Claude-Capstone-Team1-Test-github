"""Migration runner logic that needs no database (task 1.1; DD2)."""
import pytest

from app.migrate import APP_PRIVILEGES, MIGRATIONS_DIR, grant_app_privileges, list_migrations, split_sql, upgrade


def test_split_on_semicolons_between_statements():
    assert split_sql("CREATE TABLE a (i INT);\nCREATE TABLE b (j INT);\n") == [
        "CREATE TABLE a (i INT)",
        "CREATE TABLE b (j INT)",
    ]


def test_semicolons_inside_quotes_do_not_split():
    sql = "INSERT INTO t VALUES ('a;b', \"c;d\", `e;f`);"
    assert split_sql(sql) == ["INSERT INTO t VALUES ('a;b', \"c;d\", `e;f`)"]


def test_escaped_quotes_inside_strings():
    sql = "INSERT INTO t VALUES ('it\\'s; fine', 'x''y;z');"
    assert split_sql(sql) == ["INSERT INTO t VALUES ('it\\'s; fine', 'x''y;z')"]


def test_comments_are_removed_including_semicolons_in_them():
    sql = (
        "-- header; comment\n"
        "CREATE TABLE a ( -- trailing; comment\n"
        "  i INT /* block; comment */\n"
        ");\n"
    )
    statements = split_sql(sql)
    assert len(statements) == 1
    assert "comment" not in statements[0]
    assert statements[0].startswith("CREATE TABLE a (")


def test_double_dash_without_space_is_not_a_comment():
    assert split_sql("SELECT 1--1;") == ["SELECT 1--1"]


@pytest.mark.parametrize("bad", ["CREATE TABLE a (i INT)", "SELECT 'unterminated;", "SELECT 1; /* never closed"])
def test_malformed_migrations_are_rejected(bad):
    with pytest.raises(ValueError):
        split_sql(bad)


def test_initial_migration_parses_into_one_statement_per_table():
    statements = split_sql((MIGRATIONS_DIR / "001_initial_schema.sql").read_text(encoding="utf-8"))
    created = [s.split("(")[0].split()[-1].strip("`") for s in statements]
    assert all(s.startswith("CREATE TABLE") for s in statements)
    assert created == [
        "User", "Admin", "BankAccount", "Portfolio", "Issuer", "StockPrice",
        "Inventory", "Transaction", "Transfer", "History", "MarketSchedule", "SystemState",
    ]


def test_every_table_created_by_migrations_has_app_privileges_defined():
    created = set()
    for path in list_migrations():
        if path.suffix == ".py":
            assert "CREATE TABLE" not in path.read_text(encoding="utf-8").upper(), path.name  # keep new tables in .sql
            continue
        for statement in split_sql(path.read_text(encoding="utf-8")):
            if statement.startswith("CREATE TABLE"):
                created.add(statement.split("(")[0].split()[-1].strip("`"))
    assert created == set(APP_PRIVILEGES)


def test_app_account_can_never_change_the_audit_trail():  # R17.2, N3.6
    for table, privileges in APP_PRIVILEGES.items():
        granted = {p.strip() for p in privileges.split(",")}
        assert granted <= {"SELECT", "INSERT", "UPDATE", "DELETE"}, table
        if table != "Inventory":
            assert "DELETE" not in granted, f"{table} must not allow DELETE"
    assert "UPDATE" not in APP_PRIVILEGES["Transaction"]
    assert "UPDATE" not in APP_PRIVILEGES["History"]


def test_migration_files_must_be_numbered(tmp_path):
    (tmp_path / "001_first.sql").write_text("SELECT 1;")
    (tmp_path / "002_second.sql").write_text("SELECT 2;")
    assert [p.name for p in list_migrations(tmp_path)] == ["001_first.sql", "002_second.sql"]


def test_python_migrations_share_the_numbering(tmp_path):  # DV8
    (tmp_path / "001_first.sql").write_text("SELECT 1;")
    (tmp_path / "002_second.py").write_text("def upgrade(cursor):\n    pass\n")
    (tmp_path / "003_third.sql").write_text("SELECT 3;")
    (tmp_path / "__pycache__").mkdir()           # left behind by Python: not a migration
    (tmp_path / "notes.txt").write_text("ignored")
    assert [p.name for p in list_migrations(tmp_path)] == ["001_first.sql", "002_second.py", "003_third.sql"]


@pytest.mark.parametrize("names", [["first.sql"], ["1_first.sql"], ["001-First.sql"], ["001_a.sql", "001_b.sql"],
                                   ["002-second.py"], ["__init__.py"], ["001_a.sql", "001_a.py"]])
def test_badly_named_or_duplicate_migrations_are_rejected(tmp_path, names):
    for name in names:
        (tmp_path / name).write_text("SELECT 1;")
    with pytest.raises(ValueError):
        list_migrations(tmp_path)


@pytest.mark.parametrize("database", ["", "trading db", "x`; DROP DATABASE y; --", "a" * 65])
def test_grants_refuse_unsafe_database_names_before_touching_the_server(database):  # N2.1
    with pytest.raises(ValueError):
        grant_app_privileges(object(), database, "trading_app")

# --- upgrade() with a stand-in connection: .sql and .py run in number order ------------------------

class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def fetchall(self):
        return []  # nothing applied yet


class FakeConn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.log.append(("COMMIT", None))


def test_upgrade_runs_sql_and_python_migrations_in_order(tmp_path):  # DV8
    (tmp_path / "001_first.sql").write_text("CREATE TABLE a (i INT);")
    (tmp_path / "002_second.py").write_text(
        "def upgrade(cursor):\n    cursor.execute('ALTER TABLE a ADD j INT')\n")
    (tmp_path / "003_third.sql").write_text("CREATE TABLE c (k INT);")
    conn = FakeConn()
    assert upgrade(conn, tmp_path) == ["001_first.sql", "002_second.py", "003_third.sql"]
    work = [sql for sql, params in conn.log if not sql.startswith(("CREATE TABLE IF NOT EXISTS", "SELECT filename"))]
    recorded = "INSERT INTO schema_migrations (filename) VALUES (%s)"
    assert work == ["COMMIT",  # after reading which migrations were already applied
                    "CREATE TABLE a (i INT)", recorded, "COMMIT",
                    "ALTER TABLE a ADD j INT", recorded, "COMMIT",
                    "CREATE TABLE c (k INT)", recorded, "COMMIT"]
    assert [params for sql, params in conn.log if sql == recorded] == [
        ("001_first.sql",), ("002_second.py",), ("003_third.sql",)]


def test_python_migration_without_upgrade_is_rejected_and_not_recorded(tmp_path):
    (tmp_path / "001_broken.py").write_text("VALUE = 1\n")
    conn = FakeConn()
    with pytest.raises(ValueError, match="must define upgrade"):
        upgrade(conn, tmp_path)
    assert not any("schema_migrations (filename)" in sql for sql, _ in conn.log)