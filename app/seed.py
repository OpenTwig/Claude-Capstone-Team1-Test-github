"""Development sample data for `flask seed` (design: Local development, Sample data; task 4.6).

Everything goes through the normal services, so seeded data follows the same rules as data
entered on the site. Safe to run again: stocks and the demo customer that already exist are
skipped. Stocks are added in an administrator's name, so an administrator must exist first
(`flask create-admin`).
"""
from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app import clock
from app.db import get_db
from app.errors import SymbolNotFound
from app.services import bank_accounts, portfolios, stocks, transfers, users
from app.services.market import market_tz

DEMO_EMAIL = "demo.customer@example.com"
DEMO_NAME = "Demo Customer"
DEMO_DOB = date(1990, 1, 15)
OPENING_DEPOSIT = Decimal("25000.00")
SHARES_PER_STOCK = Decimal("1000000")

# Made-up companies, so nobody mistakes the simulated prices for real ones.
SAMPLE_STOCKS: tuple[tuple[str, str, str, str], ...] = (
    ("AURA", "Aurora Solar Works", "NASDAQ", "42.18"),
    ("BLDR", "Bedrock Builders Inc.", "NYSE", "87.50"),
    ("CRVX", "Cerulean Vax Labs", "NASDAQ", "15.73"),
    ("DUNE", "Dune Logistics Group", "NYSE", "63.05"),
    ("EMBR", "Ember Coffee Roasters", "NASDAQ", "9.42"),
    ("FJRD", "Fjord Maritime Holdings", "NYSE", "121.90"),
    ("GLDN", "Golden Mesa Mining", "NYSE", "5.66"),
    ("HVST", "Harvest Table Foods", "NASDAQ", "34.27"),
    ("IONQX", "Ion Quarry Semiconductors", "NASDAQ", "248.10"),
    ("JUNO", "Juniper Health Partners", "NYSE", "56.84"),
)


class SeedRefused(Exception):
    """The seed can't run here (wrong environment, or no administrator yet)."""


@dataclass
class SeedResult:
    stocks_added: list[str] = field(default_factory=list)
    stocks_skipped: list[str] = field(default_factory=list)
    demo_email: str = DEMO_EMAIL
    demo_password: str | None = None  # set only when the demo customer was created on this run


def _first_admin() -> int:
    with get_db().cursor() as cur:
        cur.execute(
            "SELECT a.userID FROM Admin a JOIN `User` u ON u.userID = a.userID"
            " WHERE u.is_active ORDER BY a.userID LIMIT 1"
        )
        found = cur.fetchone()
    get_db().commit()
    if found is None:
        raise SeedRefused("Create an administrator first: flask --app app create-admin")
    return found["userID"]


def _demo_exists() -> bool:
    with get_db().cursor() as cur:
        cur.execute("SELECT EXISTS(SELECT 1 FROM `User` WHERE email = %s) AS found", (DEMO_EMAIL,))
        found = cur.fetchone()["found"]
    get_db().commit()
    return bool(found)


def _random_ssn() -> str:
    """A made-up SSN in the 9xx range, which is never issued as a real SSN. It is stored encrypted."""
    return f"9{secrets.randbelow(100):02d}-{secrets.randbelow(100):02d}-{secrets.randbelow(10000):04d}"


def run_seed(settings, rng: random.Random | None = None) -> SeedResult:
    """Add the sample stocks and the demo customer. Needs an app context."""
    if settings.app_env != "development":
        raise SeedRefused(f"flask seed only runs with APP_ENV=development (this is {settings.app_env}).")
    admin_id = _first_admin()
    rng = rng or random.Random()
    result = SeedResult()

    for symbol, company, exchange, price in SAMPLE_STOCKS:
        try:
            stocks.get_stock(symbol, include_delisted=True)
            result.stocks_skipped.append(symbol)
            continue
        except SymbolNotFound:
            pass
        stocks.add_stock(admin_id, company, symbol, Decimal(price), "Sample company for development.",
                         exchange, randomize_history=True, shares=SHARES_PER_STOCK, rng=rng)
        result.stocks_added.append(symbol)

    if not _demo_exists():
        user_id, password = users.create_user(admin_id, DEMO_NAME, _random_ssn(), DEMO_EMAIL, DEMO_DOB)
        portfolio = portfolios.create_portfolio(user_id, "Brokerage", "Brokerage")
        bank = bank_accounts.add_bank_account(
            user_id, "Sample Bank", f"{secrets.randbelow(10**10):010d}", "021000021", "Checking")
        today = clock.now_utc().astimezone(market_tz()).date()
        transfers.deposit(user_id, bank.bank_account_id, portfolio.portfolio_id, OPENING_DEPOSIT, today)
        result.demo_password = password
    return result
