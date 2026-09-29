"""`flask seed` development sample data (task 4.6; design: Local development, Sample data)."""
import pathlib
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import create_app
from app.migrate import APP_PRIVILEGES
from app.seed import DEMO_EMAIL, OPENING_DEPOSIT, SAMPLE_STOCKS, SeedRefused, run_seed
from app.services import users
from tests.factories import make_admin

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("America/Phoenix"))
APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def counts(db) -> dict[str, int]:
    return {table: one(db, f"SELECT COUNT(*) AS n FROM `{table}`")["n"] for table in APP_PRIVILEGES}


@pytest.fixture
def dev_app(db, db_env, fixed_clock):
    fixed_clock(NOW)
    return create_app(dict(db_env, APP_ENV="development"))


def seed(app):
    return app.test_cli_runner().invoke(args=["seed"])


def test_seed_adds_stocks_and_a_demo_customer(db, dev_app):
    make_admin(db)
    result = seed(dev_app)
    assert result.exit_code == 0, result.output
    assert one(db, "SELECT COUNT(*) AS n FROM Issuer WHERE is_listed")["n"] == len(SAMPLE_STOCKS)
    # Randomize History: about a year of trading days per stock, ending at the entered price.
    for symbol, _, _, price in SAMPLE_STOCKS:
        stock = one(db, "SELECT issuerID, current_price, shares_available FROM Issuer WHERE ticker_symbol = %s", (symbol,))
        assert stock["current_price"] == Decimal(price) and stock["shares_available"] > 0
        days = one(db, "SELECT COUNT(*) AS n FROM StockPrice WHERE issuerID = %s", (stock["issuerID"],))["n"]
        assert 240 <= days <= 260
    user = one(db, "SELECT userID, must_change_password FROM `User` WHERE email = %s", (DEMO_EMAIL,))
    assert user["must_change_password"] == 1
    portfolio = one(db, "SELECT portfolioID, cash_balance, account_type FROM Portfolio WHERE userID = %s", (user["userID"],))
    assert portfolio["cash_balance"] == OPENING_DEPOSIT and portfolio["account_type"] == "Brokerage"
    # The opening deposit went through the transfers service, so the History is real.
    assert one(db, "SELECT COUNT(*) AS n FROM Transfer WHERE to_portfolioID = %s AND transfer_type = 'Deposit'"
               " AND status = 'Completed'", (portfolio["portfolioID"],))["n"] == 1
    assert one(db, "SELECT COUNT(*) AS n FROM History WHERE portfolioID = %s", (portfolio["portfolioID"],))["n"] == 1
    assert one(db, "SELECT COUNT(*) AS n FROM BankAccount WHERE userID = %s", (user["userID"],))["n"] == 1


def test_the_printed_password_logs_the_demo_customer_in(db, dev_app):
    make_admin(db)
    output = seed(dev_app).output
    password = output.strip().splitlines()[-1]
    with dev_app.app_context():
        assert users.authenticate(DEMO_EMAIL, password).email == DEMO_EMAIL


def test_running_it_twice_changes_nothing(db, dev_app):
    make_admin(db)
    assert seed(dev_app).exit_code == 0
    before = counts(db)
    second = seed(dev_app)
    assert second.exit_code == 0, second.output
    assert counts(db) == before
    assert "already exists, skipped" in second.output and "Temporary password" not in second.output


def test_refuses_outside_development(db, db_env):  # never touches a test or production database
    make_admin(db)
    before = counts(db)
    result = create_app(db_env).test_cli_runner().invoke(args=["seed"])  # APP_ENV=test
    assert result.exit_code != 0 and "only runs with APP_ENV=development" in result.output
    with pytest.raises(SeedRefused, match="APP_ENV=development"):
        run_seed(SimpleNamespace(app_env="production"))  # refused before any database access
    assert counts(db) == before


def test_needs_an_administrator_first(db, dev_app):
    result = seed(dev_app)
    assert result.exit_code != 0 and "Create an administrator first" in result.output
    assert one(db, "SELECT COUNT(*) AS n FROM Issuer")["n"] == 0


def test_no_fixed_passwords_in_code(db, dev_app):
    make_admin(db)
    password = seed(dev_app).output.strip().splitlines()[-1]
    assert len(password) >= 12
    for path in APP_DIR.rglob("*.py"):
        assert password not in path.read_text(encoding="utf-8"), path
    # A fresh database gets a different password: it is generated, not stored anywhere.
    with db.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in APP_PRIVILEGES:
            cur.execute(f"TRUNCATE TABLE `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    db.commit()
    make_admin(db)
    assert seed(dev_app).output.strip().splitlines()[-1] != password
