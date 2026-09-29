"""Portfolios (design: Portfolios; R4, A10). Every function is scoped to the owner's user_id (R2.5)."""
from __future__ import annotations

import secrets

import pymysql
from flask import current_app

from app.db import get_db, transaction
from app.errors import NotFound, ValidationFailed
from app.services import validation
from app.services.types import PortfolioRow
from app.services.users import require_customer

DUPLICATE_KEY = 1062
ACCOUNT_NUMBER_ATTEMPTS = 5

_COLUMNS = "portfolioID, userID, account_name, account_type, account_number, cash_balance, created_at"


def generate_account_number() -> str:
    """A random 10-digit account number (A10)."""
    return f"{secrets.randbelow(10**10):010d}"


def _row(r: dict) -> PortfolioRow:
    return PortfolioRow(
        portfolio_id=r["portfolioID"],
        user_id=r["userID"],
        account_name=r["account_name"],
        account_type=r["account_type"],
        account_number=r["account_number"],
        cash_balance=r["cash_balance"],
        created_at=r["created_at"],
    )


def create_portfolio(user_id: int, account_name: str, account_type: str) -> PortfolioRow:
    """Open a portfolio with $0.00 cash and a unique account number (R4.2-R4.4). Customers only (D19)."""
    require_customer(user_id)
    name = validation.text("account_name", account_name, "Account name", 100)
    allowed = current_app.config["ACCOUNT_TYPES"]
    if not allowed:
        raise ValidationFailed("account_type", "Portfolio types have not been set up yet. Please try again later.")
    if account_type not in allowed:
        raise ValidationFailed("account_type", "Choose an account type.")

    for attempt in range(ACCOUNT_NUMBER_ATTEMPTS):
        try:
            with transaction() as cur:
                cur.execute(
                    "INSERT INTO Portfolio (userID, cash_balance, account_name, account_type, account_number)"
                    " VALUES (%s, 0.00, %s, %s, %s)",
                    (user_id, name, account_type, generate_account_number()),
                )
                new_id = cur.lastrowid
        except pymysql.err.IntegrityError as exc:
            if exc.args[0] == DUPLICATE_KEY and attempt < ACCOUNT_NUMBER_ATTEMPTS - 1:
                continue  # account number already taken: try another
            raise
        return get_owned_portfolio(user_id, new_id)
    raise RuntimeError("unreachable")


def list_portfolios(user_id: int) -> list[PortfolioRow]:
    with get_db().cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM Portfolio WHERE userID = %s ORDER BY created_at, portfolioID", (user_id,))
        rows = cur.fetchall()
    get_db().commit()
    return [_row(r) for r in rows]


def get_owned_portfolio(user_id: int, portfolio_id: int) -> PortfolioRow:
    """The portfolio, if it belongs to user_id; otherwise NotFound (never reveals that it exists)."""
    with get_db().cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM Portfolio WHERE portfolioID = %s AND userID = %s", (portfolio_id, user_id))
        found = cur.fetchone()
    get_db().commit()
    if found is None:
        raise NotFound()
    return _row(found)


def set_reinvest(user_id: int, portfolio_id: int, issuer_id: int, on: bool) -> None:
    """Save a position's Reinvest? choice (R6.7, D22). It changes nothing else: there are no dividends.

    Someone else's portfolio, or a position that doesn't exist, is NotFound (R2.5).
    """
    get_owned_portfolio(user_id, portfolio_id)
    with transaction() as cur:
        cur.execute("SELECT 1 FROM Inventory WHERE portfolioID = %s AND issuerID = %s FOR UPDATE",
                    (portfolio_id, issuer_id))
        if cur.fetchone() is None:
            raise NotFound()
        cur.execute("UPDATE Inventory SET reinvest = %s WHERE portfolioID = %s AND issuerID = %s",
                    (on, portfolio_id, issuer_id))
