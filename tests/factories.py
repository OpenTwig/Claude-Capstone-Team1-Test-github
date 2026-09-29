"""Insert test rows directly (task 1.1). Each factory commits and returns the new row's ID.

These bypass the services on purpose, so a test can set up any state, including
states the app itself would refuse to create.
"""
from __future__ import annotations

import itertools
from datetime import date
from decimal import Decimal

_counter = itertools.count(1)


def _next() -> int:
    return next(_counter)


def _insert(conn, sql: str, params: tuple) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        new_id = cur.lastrowid
    conn.commit()
    return new_id


def make_user(
    conn,
    *,
    name: str = "Test Customer",
    email: str | None = None,
    ssn: str = "not-a-real-ciphertext",
    date_of_birth: date = date(1990, 1, 1),
    password_hash: str = "not-a-real-hash",
    is_active: bool = True,
) -> int:
    email = email or f"user{_next()}@example.test"
    return _insert(
        conn,
        "INSERT INTO `User` (name, date_of_birth, ssn, email, password_hash, is_active) VALUES (%s, %s, %s, %s, %s, %s)",
        (name, date_of_birth, ssn, email, password_hash, is_active),
    )


def make_admin(conn, *, user_id: int | None = None, access_level: str = "admin", department: str = "IT") -> tuple[int, int]:
    """Returns (adminID, userID)."""
    user_id = user_id or make_user(conn, name="Test Admin")
    admin_id = _insert(
        conn,
        "INSERT INTO Admin (userID, access_level, department) VALUES (%s, %s, %s)",
        (user_id, access_level, department),
    )
    return admin_id, user_id


def make_portfolio(
    conn,
    user_id: int,
    *,
    account_name: str = "Brokerage",
    account_type: str = "Brokerage",
    cash_balance: Decimal = Decimal("0.00"),
    account_number: str | None = None,
) -> int:
    account_number = account_number or f"{9000000000 + _next()}"
    return _insert(
        conn,
        "INSERT INTO Portfolio (userID, cash_balance, account_name, account_type, account_number) VALUES (%s, %s, %s, %s, %s)",
        (user_id, cash_balance, account_name, account_type, account_number),
    )


def make_bank_account(
    conn,
    user_id: int,
    *,
    bank_name: str = "Test Bank",
    nickname: str = "Checking",
    account_number: str = "not-a-real-ciphertext",
    routing_number: str = "123456789",
) -> int:
    return _insert(
        conn,
        "INSERT INTO BankAccount (userID, bank_name, nickname, account_number, routing_number) VALUES (%s, %s, %s, %s, %s)",
        (user_id, bank_name, nickname, account_number, routing_number),
    )


def make_stock(
    conn,
    *,
    symbol: str | None = None,
    company_name: str = "Test Corp",
    price: Decimal = Decimal("100.00"),
    description: str | None = None,
    exchange: str | None = None,
    shares_available: Decimal = Decimal("1000000"),
    is_listed: bool = True,
) -> int:
    symbol = symbol or f"T{_next()}"
    return _insert(
        conn,
        "INSERT INTO Issuer (company_name, ticker_symbol, exchange, current_price, description, shares_available, is_listed)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (company_name, symbol, exchange, price, description, shares_available, is_listed),
    )
