"""Deposits and withdrawals between a customer's bank accounts and portfolios, and moves
between two of the customer's own portfolios (design: Transfers; R11, R12, R19, A5, D2, D15;
properties P1, P2, P4, P9).

Each transfer runs in one database transaction under a row lock on the portfolio:
the Transfer is created Pending, the cash balance changes, the Transfer is marked
Completed, and a History row is written. Any failure rolls all of it back. Money
is recorded, not actually sent to a bank.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app import clock
from app.db import get_db, retry_once_on_lock_conflict, transaction
from app.errors import InsufficientFunds, NoBankAccount, NotFound, ValidationFailed
from app.money import CENT, MAX_MONEY, MAX_MONEY_TEXT, TOO_MUCH_MONEY, to_cents
from app.services.market import market_tz
from app.services.types import TransferResult


def _validated_amount(amount: Decimal) -> Decimal:
    if isinstance(amount, Decimal) and amount.is_finite() and amount > MAX_MONEY:
        raise ValidationFailed("amount", TOO_MUCH_MONEY)
    if (
        not isinstance(amount, Decimal)
        or not amount.is_finite()
        or amount < CENT
        or to_cents(amount) != amount
    ):
        raise ValidationFailed("amount", "Enter an amount greater than $0.00 with at most 2 decimal places.")
    return amount


def _validated_date(transfer_date: date | None, now_utc: datetime) -> date:
    today = now_utc.astimezone(market_tz()).date()
    if transfer_date != today:  # A5: transfers happen today, in Arizona time
        raise ValidationFailed("transfer_date", f"Transfer date must be today ({today:%m/%d/%Y}).")
    return transfer_date


def _require_bank_account(cur, user_id: int, bank_account_id: int) -> None:
    cur.execute("SELECT userID, is_active FROM BankAccount WHERE bankAccountID = %s", (bank_account_id,))
    found = cur.fetchone()
    if found is None or found["userID"] != user_id or not found["is_active"]:
        cur.execute("SELECT COUNT(*) AS n FROM BankAccount WHERE userID = %s AND is_active", (user_id,))
        if cur.fetchone()["n"] == 0:
            raise NoBankAccount()  # R11.6
        raise NotFound()  # someone else's, removed (R10.7), or no such account (R2.5)


def _lock_portfolio(cur, user_id: int, portfolio_id: int) -> dict:
    cur.execute(
        "SELECT cash_balance, account_name FROM Portfolio WHERE portfolioID = %s AND userID = %s FOR UPDATE",
        (portfolio_id, user_id),
    )
    found = cur.fetchone()
    if found is None:
        raise NotFound()
    return found


def _record(cur, *, kind: str, portfolio_id: int, bank_account_id: int, amount: Decimal, transfer_date: date,
            new_balance: Decimal, now: datetime) -> int:
    from_portfolio, to_portfolio = (None, portfolio_id) if kind == "Deposit" else (portfolio_id, None)
    cur.execute(
        "INSERT INTO Transfer (from_portfolioID, to_portfolioID, bankAccountID, amount, transfer_date, status,"
        " created_at, transfer_type) VALUES (%s, %s, %s, %s, %s, 'Pending', %s, %s)",
        (from_portfolio, to_portfolio, bank_account_id, amount, transfer_date, now, kind),
    )
    transfer_id = cur.lastrowid
    cur.execute("UPDATE Portfolio SET cash_balance = %s WHERE portfolioID = %s", (new_balance, portfolio_id))
    cur.execute("UPDATE Transfer SET status = 'Completed' WHERE transferID = %s", (transfer_id,))
    cur.execute(
        "INSERT INTO History (portfolioID, transferID, action, `timestamp`) VALUES (%s, %s, %s, %s)",
        (portfolio_id, transfer_id, kind, now),
    )
    return transfer_id


@retry_once_on_lock_conflict
def deposit(user_id: int, bank_account_id: int, portfolio_id: int, amount: Decimal, transfer_date: date,
            at_utc: datetime | None = None) -> TransferResult:
    """Move money from the customer's bank account into their portfolio (R11)."""
    now_utc = at_utc or clock.now_utc()
    amount = _validated_amount(amount)
    transfer_date = _validated_date(transfer_date, now_utc)
    now = now_utc.replace(tzinfo=None)
    with transaction() as cur:
        _require_bank_account(cur, user_id, bank_account_id)
        portfolio = _lock_portfolio(cur, user_id, portfolio_id)
        new_balance = portfolio["cash_balance"] + amount
        if new_balance > MAX_MONEY:
            raise ValidationFailed("amount", f"That deposit would take the cash balance over the maximum of {MAX_MONEY_TEXT}.")
        transfer_id = _record(cur, kind="Deposit", portfolio_id=portfolio_id, bank_account_id=bank_account_id,
                              amount=amount, transfer_date=transfer_date, new_balance=new_balance, now=now)
    return TransferResult(transfer_id, "Deposit", amount, new_balance, portfolio["account_name"])


@retry_once_on_lock_conflict
def withdraw(user_id: int, portfolio_id: int, bank_account_id: int, amount: Decimal, transfer_date: date,
             at_utc: datetime | None = None) -> TransferResult:
    """Move money from the customer's portfolio to their bank account (R12). Never overdraws (P2)."""
    now_utc = at_utc or clock.now_utc()
    amount = _validated_amount(amount)
    transfer_date = _validated_date(transfer_date, now_utc)
    now = now_utc.replace(tzinfo=None)
    with transaction() as cur:
        _require_bank_account(cur, user_id, bank_account_id)
        portfolio = _lock_portfolio(cur, user_id, portfolio_id)  # locked: concurrent withdrawals queue here
        if amount > portfolio["cash_balance"]:
            raise InsufficientFunds()  # R12.3
        new_balance = portfolio["cash_balance"] - amount
        transfer_id = _record(cur, kind="Withdrawal", portfolio_id=portfolio_id, bank_account_id=bank_account_id,
                              amount=amount, transfer_date=transfer_date, new_balance=new_balance, now=now)
    return TransferResult(transfer_id, "Withdrawal", amount, new_balance, portfolio["account_name"])


@retry_once_on_lock_conflict
def move_between(user_id: int, from_portfolio_id: int, to_portfolio_id: int, amount: Decimal,
                 transfer_date: date, at_utc: datetime | None = None) -> TransferResult:
    """Move cash from one of the customer's portfolios to another of theirs (R19, D15). No bank is involved."""
    now_utc = at_utc or clock.now_utc()
    if from_portfolio_id == to_portfolio_id:
        raise ValidationFailed("to_account", "Choose a different account to move the money to.")  # R19.2
    amount = _validated_amount(amount)
    transfer_date = _validated_date(transfer_date, now_utc)
    now = now_utc.replace(tzinfo=None)
    with transaction() as cur:
        # Lock both rows in portfolioID order, so two opposite moves can't deadlock.
        locked = {pid: _lock_portfolio(cur, user_id, pid) for pid in sorted((from_portfolio_id, to_portfolio_id))}
        source, target = locked[from_portfolio_id], locked[to_portfolio_id]
        if amount > source["cash_balance"]:
            raise InsufficientFunds()  # R19.2
        from_balance = source["cash_balance"] - amount
        to_balance = target["cash_balance"] + amount
        if to_balance > MAX_MONEY:
            raise ValidationFailed("amount", f"That transfer would take the cash balance over the maximum of {MAX_MONEY_TEXT}.")
        cur.execute(
            "INSERT INTO Transfer (from_portfolioID, to_portfolioID, bankAccountID, amount, transfer_date, status,"
            " created_at, transfer_type) VALUES (%s, %s, NULL, %s, %s, 'Pending', %s, 'Internal')",
            (from_portfolio_id, to_portfolio_id, amount, transfer_date, now),
        )
        transfer_id = cur.lastrowid
        cur.execute("UPDATE Portfolio SET cash_balance = %s WHERE portfolioID = %s", (from_balance, from_portfolio_id))
        cur.execute("UPDATE Portfolio SET cash_balance = %s WHERE portfolioID = %s", (to_balance, to_portfolio_id))
        cur.execute("UPDATE Transfer SET status = 'Completed' WHERE transferID = %s", (transfer_id,))
        cur.executemany(  # R19.4: one History row on each account
            "INSERT INTO History (portfolioID, transferID, action, `timestamp`) VALUES (%s, %s, %s, %s)",
            [(from_portfolio_id, transfer_id, "Transfer out", now), (to_portfolio_id, transfer_id, "Transfer in", now)],
        )
    return TransferResult(transfer_id, "Internal", amount, from_balance, source["account_name"],
                          to_balance, target["account_name"])


def has_bank_account(user_id: int) -> bool:
    with get_db().cursor() as cur:
        cur.execute("SELECT EXISTS(SELECT 1 FROM BankAccount WHERE userID = %s AND is_active) AS found", (user_id,))
        found = bool(cur.fetchone()["found"])
    get_db().commit()
    return found
