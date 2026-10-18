"""Schema, migrations, constraints, privileges, and db access against real MySQL (task 1.1).

Requirements: N2.4 (database constraints), N3.6 (least privilege), N4 (precision),
R17.2 (audit trail cannot be changed through the app).
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pymysql
import pytest

from app import create_app
from app.db import get_db, transaction
from app.migrate import APP_PRIVILEGES, list_migrations, upgrade
from tests.factories import make_admin, make_bank_account, make_portfolio, make_stock, make_user

pytestmark = pytest.mark.db

CHECK_VIOLATION = 3819
DUPLICATE_KEY = 1062
FOREIGN_KEY_MISSING = 1452
ACCESS_DENIED = 1142  # command denied to user for table


@contextmanager
def mysql_error(conn, *codes):
    """Expect a MySQL error with one of `codes`, then roll back so the connection stays usable."""
    with pytest.raises(pymysql.err.MySQLError) as exc:
        yield
    conn.rollback()
    assert exc.value.args[0] in codes, f"expected MySQL error {codes}, got {exc.value.args}"


def run(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# --- Migrations ------------------------------------------------------------------------------

def test_fresh_database_has_exactly_the_designed_tables(db, test_database):
    rows = run(db, "SELECT table_name AS t FROM information_schema.tables WHERE table_schema = %s",
               (test_database.settings.db_name,))
    # MySQL on Windows stores table names in lower case (lower_case_table_names=1), so compare case-insensitively.
    assert {r["t"].lower() for r in rows} == {t.lower() for t in APP_PRIVILEGES} | {"schema_migrations"}


def test_migrations_were_applied_from_scratch_and_recorded(db, test_database):
    """Every migration file, in order, applies to an empty database and is recorded."""
    expected = [p.name for p in list_migrations()]
    assert expected[0] == "001_initial_schema.sql"
    assert test_database.applied == expected
    recorded = [r["filename"] for r in run(db, "SELECT filename FROM schema_migrations ORDER BY filename")]
    assert recorded == expected


def test_running_the_upgrade_again_changes_nothing(db):
    assert upgrade(db) == []


def test_db_upgrade_cli_command(db_env):
    result = create_app(db_env).test_cli_runner().invoke(args=["db-upgrade"])
    assert result.exit_code == 0, result.output
    assert "No new migrations for trading_test" in result.output
    assert "Table privileges for trading_app are up to date" in result.output


def test_db_upgrade_cli_requires_migration_credentials(valid_env):
    result = create_app(valid_env).test_cli_runner().invoke(args=["db-upgrade"])
    assert result.exit_code != 0
    assert "DB_MIGRATE_USER and DB_MIGRATE_PASSWORD must be set" in result.output


# --- Constraints (N2.4) --------------------------------------------------------------------------

@pytest.fixture
def owner(db):
    user_id = make_user(db)
    return SimpleIds(user=user_id, portfolio=make_portfolio(db, user_id), bank=make_bank_account(db, user_id), stock=make_stock(db))


class SimpleIds:
    def __init__(self, **ids):
        self.__dict__.update(ids)


def test_cash_balance_cannot_go_negative(db, owner):  # P2
    with mysql_error(db, CHECK_VIOLATION):
        run(db, "UPDATE Portfolio SET cash_balance = -0.01 WHERE portfolioID = %s", (owner.portfolio,))


@pytest.mark.parametrize("symbol", ["aapl", "AB-C", "A B", "ÄBC"])
def test_ticker_must_be_uppercase_letters_and_digits(db, symbol):  # R14.2
    with mysql_error(db, CHECK_VIOLATION):
        make_stock(db, symbol=symbol)


def test_ticker_longer_than_ten_characters_is_rejected(db):
    with mysql_error(db, 1406):  # data too long (strict mode)
        make_stock(db, symbol="ABCDEFGHIJK")


def test_stock_price_must_be_at_least_one_cent(db):  # R16.3
    with mysql_error(db, CHECK_VIOLATION):
        make_stock(db, price=Decimal("0.00"))


def test_zero_quantity_position_cannot_exist(db, owner):  # R9.6: positions at zero are deleted
    with mysql_error(db, CHECK_VIOLATION):
        run(db, "INSERT INTO Inventory (portfolioID, issuerID, quantity, average_cost) VALUES (%s, %s, 0, 10.00)",
            (owner.portfolio, owner.stock))


def test_one_position_per_portfolio_and_stock(db, owner):
    sql = "INSERT INTO Inventory (portfolioID, issuerID, quantity, average_cost) VALUES (%s, %s, 1, 10.00)"
    run(db, sql, (owner.portfolio, owner.stock))
    db.commit()
    with mysql_error(db, DUPLICATE_KEY):
        run(db, sql, (owner.portfolio, owner.stock))


@pytest.mark.parametrize("routing", ["12345678", "1234567890", "12345678a", "abcdefghi"])
def test_routing_number_must_be_nine_digits(db, owner, routing):  # R10.2
    with mysql_error(db, CHECK_VIOLATION, 1406):
        make_bank_account(db, owner.user, routing_number=routing)


def insert_transaction(conn, owner, **overrides):
    row = dict(type="Buy", quantity=Decimal("1"), price=Decimal("10.00"), total=Decimal("10.00"),
               status="Filled", order_type="Market", timing="Day Only")
    row.update(overrides)
    run(conn,
        "INSERT INTO `Transaction` (portfolioID, issuerID, type, quantity, price_per_share, total_amount, status,"
        " order_type, timing) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (owner.portfolio, owner.stock, row["type"], row["quantity"], row["price"], row["total"], row["status"],
         row["order_type"], row["timing"]))


@pytest.mark.parametrize(
    "overrides",
    [{"type": "Hold"}, {"status": "Rejected"}, {"order_type": "Limit"}, {"timing": "GTC"},
     {"quantity": Decimal("0")}, {"total": Decimal("0.00")}],
)
def test_transaction_values_are_restricted(db, owner, overrides):  # A4, reference values
    with mysql_error(db, CHECK_VIOLATION):
        insert_transaction(db, owner, **overrides)


def insert_transfer(conn, transfer_type, *, from_p=None, to_p=None, bank=None, amount=Decimal("10.00"), status="Completed"):
    run(conn,
        "INSERT INTO Transfer (from_portfolioID, to_portfolioID, bankAccountID, amount, transfer_date, status,"
        " transfer_type) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (from_p, to_p, bank, amount, date(2026, 9, 28), status, transfer_type))


def test_valid_deposit_and_withdrawal_rows_are_accepted(db, owner):
    insert_transfer(db, "Deposit", to_p=owner.portfolio, bank=owner.bank)
    insert_transfer(db, "Withdrawal", from_p=owner.portfolio, bank=owner.bank)
    db.commit()


@pytest.mark.parametrize(
    "transfer_type, kwargs",
    [
        ("Deposit", {"to_p": "P"}),                               # deposit without a bank account
        ("Deposit", {"from_p": "P", "bank": "B"}),                # deposit into nothing
        ("Withdrawal", {"to_p": "P", "bank": "B"}),               # withdrawal must come from a portfolio
        ("Deposit", {"from_p": "P", "to_p": "P", "bank": "B"}),   # a deposit never comes from a portfolio
        ("Refund", {"to_p": "P", "bank": "B"}),                   # unknown type
    ],
)
def test_transfer_shape_is_enforced(db, owner, transfer_type, kwargs):
    resolved = {k: owner.portfolio if v == "P" else owner.bank for k, v in kwargs.items()}
    with mysql_error(db, CHECK_VIOLATION):
        insert_transfer(db, transfer_type, **resolved)


@pytest.fixture
def second_portfolio(db, owner):
    return make_portfolio(db, owner.user, account_name="Second")


def test_internal_transfer_between_two_portfolios_is_accepted(db, owner, second_portfolio):  # D15, DV8
    insert_transfer(db, "Internal", from_p=owner.portfolio, to_p=second_portfolio)
    db.commit()


@pytest.mark.parametrize("kwargs", [
    {"from_p": "P", "to_p": "Q", "bank": "B"},   # a move between own accounts never involves a bank
    {"from_p": "P", "to_p": "P"},                # an account to itself
    {"from_p": "P"},                             # to nowhere
    {"to_p": "Q"},                               # from nowhere
])
def test_internal_transfer_shape_is_enforced(db, owner, second_portfolio, kwargs):  # R19.1, R19.2
    ids = {"P": owner.portfolio, "Q": second_portfolio, "B": owner.bank}
    with mysql_error(db, CHECK_VIOLATION):
        insert_transfer(db, "Internal", **{k: ids[v] for k, v in kwargs.items()})


def test_an_internal_transfer_has_one_history_row_per_account(db, owner, second_portfolio):  # R19.4, DV8
    insert_transfer(db, "Internal", from_p=owner.portfolio, to_p=second_portfolio)
    transfer_id = run(db, "SELECT LAST_INSERT_ID() AS id")[0]["id"]
    sql = "INSERT INTO History (portfolioID, transferID, action) VALUES (%s, %s, %s)"
    run(db, sql, (owner.portfolio, transfer_id, "Transfer out"))
    run(db, sql, (second_portfolio, transfer_id, "Transfer in"))
    db.commit()
    with mysql_error(db, DUPLICATE_KEY):
        run(db, sql, (owner.portfolio, transfer_id, "Transfer out"))  # but only one per account


def test_migration_005_replaced_the_generated_shape_check(db):  # DV8
    checks = run(db,
                 "SELECT cc.CONSTRAINT_NAME AS name, cc.CHECK_CLAUSE AS clause"
                 " FROM information_schema.CHECK_CONSTRAINTS cc JOIN information_schema.TABLE_CONSTRAINTS tc"
                 "   ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME"
                 " WHERE cc.CONSTRAINT_SCHEMA = DATABASE() AND LOWER(tc.TABLE_NAME) = 'transfer'")
    shape = [c["name"] for c in checks if "Withdrawal" in c["clause"]]
    assert shape == ["transfer_shape"]  # the old generated one is gone, not left alongside
    assert len(checks) == 3             # amount, status, shape


@pytest.mark.parametrize("field", [{"amount": Decimal("0.00")}, {"status": "Failed"}])
def test_transfer_amount_and_status_are_restricted(db, owner, field):
    with mysql_error(db, CHECK_VIOLATION):
        insert_transfer(db, "Deposit", to_p=owner.portfolio, bank=owner.bank, **field)


def test_history_must_reference_exactly_one_event(db, owner):  # R17.1
    insert_transaction(db, owner)
    transaction_id = run(db, "SELECT LAST_INSERT_ID() AS id")[0]["id"]
    insert_transfer(db, "Deposit", to_p=owner.portfolio, bank=owner.bank)
    transfer_id = run(db, "SELECT LAST_INSERT_ID() AS id")[0]["id"]
    db.commit()
    sql = "INSERT INTO History (portfolioID, transactionID, transferID, action) VALUES (%s, %s, %s, %s)"
    with mysql_error(db, CHECK_VIOLATION):
        run(db, sql, (owner.portfolio, transaction_id, transfer_id, "Buy"))   # both
    with mysql_error(db, CHECK_VIOLATION):
        run(db, sql, (owner.portfolio, None, None, "Buy"))                    # neither
    run(db, sql, (owner.portfolio, transaction_id, None, "Buy"))
    db.commit()
    with mysql_error(db, DUPLICATE_KEY):
        run(db, sql, (owner.portfolio, transaction_id, None, "Buy"))          # a second row for the same trade


def test_one_price_row_per_stock_per_day(db, owner):  # R16.7
    sql = "INSERT INTO StockPrice (issuerID, price_date, open_price, close_price) VALUES (%s, %s, 10.00, 10.00)"
    run(db, sql, (owner.stock, date(2026, 9, 28)))
    db.commit()
    with mysql_error(db, DUPLICATE_KEY):
        run(db, sql, (owner.stock, date(2026, 9, 28)))


def test_market_schedule_close_must_be_after_open(db, owner):  # R15.5
    with mysql_error(db, CHECK_VIOLATION):
        run(db, "INSERT INTO MarketSchedule (open_time, close_time, updated_by) VALUES ('17:00', '09:00', %s)", (owner.user,))


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO `User` (name, date_of_birth, ssn, email, password_hash) VALUES ('X', '1990-01-01', 's', 'dup@example.test', 'h')",
        "INSERT INTO Issuer (company_name, ticker_symbol, current_price, shares_available) VALUES ('X', 'DUP', 1.00, 100)",
        "INSERT INTO Portfolio (userID, account_name, account_type, account_number) VALUES ({user}, 'X', 'Brokerage', '1111111111')",
    ],
)
def test_unique_values_are_enforced(db, owner, sql):  # R3.3, R14.4, R4.4
    sql = sql.format(user=owner.user)
    run(db, sql)
    db.commit()
    with mysql_error(db, DUPLICATE_KEY):
        run(db, sql)


def test_emails_are_unique_regardless_of_case(db):
    make_user(db, email="Case@Example.test")
    with mysql_error(db, DUPLICATE_KEY):
        make_user(db, email="case@example.TEST")


def test_share_supply_is_required(db):  # D16, DV9
    with mysql_error(db, 1364):  # field has no default value (strict mode)
        run(db, "INSERT INTO Issuer (company_name, ticker_symbol, current_price) VALUES ('X', 'NOSHR', 1.00)")


def test_new_stocks_are_listed_by_default(db):  # D20
    make_stock(db, symbol="LIST")
    assert run(db, "SELECT is_listed FROM Issuer WHERE ticker_symbol = 'LIST'")[0]["is_listed"] == 1


def test_foreign_keys_are_enforced(db):
    with mysql_error(db, FOREIGN_KEY_MISSING):
        make_portfolio(db, user_id=999999)


def test_required_columns_are_enforced(db):
    with mysql_error(db, 1048, 1364):  # column cannot be null / has no default (strict mode)
        run(db, "INSERT INTO `User` (name, date_of_birth, ssn, password_hash) VALUES ('X', '1990-01-01', 's', 'h')")


# --- App account privileges (N3.6, R17.2) --------------------------------------------------------

@pytest.fixture
def audit_rows(db, owner):
    insert_transaction(db, owner)
    transaction_id = run(db, "SELECT LAST_INSERT_ID() AS id")[0]["id"]
    insert_transfer(db, "Deposit", to_p=owner.portfolio, bank=owner.bank)
    transfer_id = run(db, "SELECT LAST_INSERT_ID() AS id")[0]["id"]
    run(db, "INSERT INTO History (portfolioID, transactionID, action) VALUES (%s, %s, 'Buy')", (owner.portfolio, transaction_id))
    run(db, "INSERT INTO Inventory (portfolioID, issuerID, quantity, average_cost) VALUES (%s, %s, 1, 10.00)",
        (owner.portfolio, owner.stock))
    db.commit()
    return SimpleIds(transaction=transaction_id, transfer=transfer_id, **owner.__dict__)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE `Transaction` SET total_amount = 1.00",
        "DELETE FROM `Transaction`",
        "UPDATE History SET action = 'Sell'",
        "DELETE FROM History",
        "DELETE FROM Transfer",
        "DELETE FROM Portfolio",
        "DELETE FROM `User`",
        "DROP TABLE Issuer",
        "ALTER TABLE Portfolio DROP CHECK portfolio_chk_1",
        "SELECT * FROM schema_migrations",
    ],
)
def test_app_account_is_denied_what_it_must_never_do(app_db, audit_rows, sql):  # R17.2, N3.6
    with mysql_error(app_db, ACCESS_DENIED):
        run(app_db, sql)


def test_app_account_cannot_create_tables(app_db):
    with mysql_error(app_db, 1044, ACCESS_DENIED):  # no database-level CREATE
        run(app_db, "CREATE TABLE sneaky (i INT)")


def test_app_account_can_do_its_normal_work(app_db, audit_rows):
    assert run(app_db, "SELECT COUNT(*) AS n FROM History")[0]["n"] == 1
    run(app_db, "INSERT INTO History (portfolioID, transferID, action) VALUES (%s, %s, 'Deposit')",
        (audit_rows.portfolio, audit_rows.transfer))
    run(app_db, "UPDATE Transfer SET status = 'Completed' WHERE transferID = %s", (audit_rows.transfer,))
    run(app_db, "UPDATE Portfolio SET cash_balance = cash_balance + 10 WHERE portfolioID = %s", (audit_rows.portfolio,))
    run(app_db, "DELETE FROM Inventory WHERE portfolioID = %s", (audit_rows.portfolio,))  # positions at zero
    app_db.commit()


# --- Database access layer (db.py) ------------------------------------------------------------

def test_sessions_run_in_utc_with_millisecond_timestamps(app_db):  # DD7, N4.3
    row = run(app_db, "SELECT @@session.time_zone AS tz, CURRENT_TIMESTAMP(3) AS now")[0]
    assert row["tz"] == "+00:00"
    assert abs(row["now"].replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)) < timedelta(seconds=5)


def test_money_comes_back_as_exact_decimals(db, owner):  # N4.1
    run(db, "UPDATE Portfolio SET cash_balance = 1234.56 WHERE portfolioID = %s", (owner.portfolio,))
    db.commit()
    value = run(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (owner.portfolio,))[0]["cash_balance"]
    assert isinstance(value, Decimal) and value == Decimal("1234.56")


def test_created_timestamps_keep_milliseconds(db):
    user_id = make_user(db)
    signup = run(db, "SELECT signup FROM `User` WHERE userID = %s", (user_id,))[0]["signup"]
    assert signup.microsecond % 1000 == 0  # DATETIME(3): milliseconds, not whole seconds only


def test_transaction_commits_on_success(db_env, db):
    app = create_app(db_env)
    with app.app_context():
        with transaction() as cur:
            cur.execute("INSERT INTO SystemState (state_key, state_value) VALUES ('k', 'v')")
    assert run(db, "SELECT state_value FROM SystemState WHERE state_key = 'k'")[0]["state_value"] == "v"


def test_transaction_rolls_back_everything_on_error(db_env, db):  # P9
    app = create_app(db_env)
    with app.app_context():
        with pytest.raises(RuntimeError):
            with transaction() as cur:
                cur.execute("INSERT INTO SystemState (state_key, state_value) VALUES ('k', 'v')")
                raise RuntimeError("boom")
    assert run(db, "SELECT COUNT(*) AS n FROM SystemState")[0]["n"] == 0


def test_one_connection_per_request_closed_at_teardown(db_env, db):
    app = create_app(db_env)
    with app.app_context():
        conn = get_db()
        assert get_db() is conn
    assert not conn.open


def test_admin_factory_links_to_a_user(db):
    admin_id, user_id = make_admin(db)
    row = run(db, "SELECT u.name FROM Admin a JOIN `User` u ON u.userID = a.userID WHERE a.adminID = %s", (admin_id,))
    assert row[0]["name"] == "Test Admin"


@pytest.mark.parametrize("days", [0, 128])
def test_market_schedule_needs_one_to_seven_trading_days(db, owner, days):  # R15.5, DV12
    with mysql_error(db, CHECK_VIOLATION):
        run(db, "INSERT INTO MarketSchedule (open_time, close_time, trading_days, updated_by) VALUES ('09:00', '17:00', %s, %s)",
            (days, owner.user))


def test_market_schedule_defaults_to_monday_to_friday_on_schedule(db, owner):  # DV12, AR05
    run(db, "INSERT INTO MarketSchedule (open_time, close_time, updated_by) VALUES ('09:00', '17:00', %s)", (owner.user,))
    with db.cursor() as cur:
        cur.execute("SELECT * FROM MarketSchedule")
        saved = cur.fetchone()
    db.commit()
    assert (saved["trading_days"], saved["market_override"]) == (31, "Schedule")
    assert "force_open" not in saved                       # replaced by market_override (migration 009)


@pytest.mark.parametrize("override", ["", "open", "Halted"])
def test_market_override_is_restricted(db, owner, override):  # R15.7, DV12
    with mysql_error(db, CHECK_VIOLATION):
        run(db, "INSERT INTO MarketSchedule (open_time, close_time, market_override, updated_by) VALUES ('09:00', '17:00', %s, %s)",
            (override, owner.user))
