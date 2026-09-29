"""Linked bank accounts (design: Bank accounts; R10, D25). Account numbers are encrypted at rest
and only ever returned masked to the last 4 digits (R10.5, N1.3). Removing an account hides it
(is_active = FALSE) instead of deleting it, so past transfers keep their bank account (R10.7).
"""
from __future__ import annotations

from app.crypto import decrypt_field, encrypt_field, mask_last4
from app.db import get_db, transaction
from app.errors import NotFound
from app.services import validation
from app.services.types import BankAccountRow
from app.services.users import require_customer

_COLUMNS = "bankAccountID, userID, bank_name, nickname, account_number, routing_number, created_at"


def _row(r: dict) -> BankAccountRow:
    return BankAccountRow(
        bank_account_id=r["bankAccountID"],
        user_id=r["userID"],
        bank_name=r["bank_name"],
        nickname=r["nickname"],
        masked_account_number=mask_last4(decrypt_field(r["account_number"])),
        routing_number=r["routing_number"],
        created_at=r["created_at"],
    )


def add_bank_account(
    user_id: int, bank_name: str, account_number: str, routing_number: str, nickname: str
) -> BankAccountRow:
    """Link a bank account (R10.1-R10.3). Customers only (D19)."""
    require_customer(user_id)
    bank_name = validation.text("bank_name", bank_name, "Bank name", 100)
    account_number = validation.bank_account_number(account_number)
    routing_number = validation.routing_number(routing_number)
    nickname = validation.text("nickname", nickname, "Nickname", 100)
    with transaction() as cur:
        cur.execute(
            "INSERT INTO BankAccount (userID, bank_name, nickname, account_number, routing_number)"
            " VALUES (%s, %s, %s, %s, %s)",
            (user_id, bank_name, nickname, encrypt_field(account_number), routing_number),
        )
        new_id = cur.lastrowid
    return get_owned_bank_account(user_id, new_id)


def list_bank_accounts(user_id: int) -> list[BankAccountRow]:
    with get_db().cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM BankAccount WHERE userID = %s AND is_active ORDER BY created_at, bankAccountID",
            (user_id,),
        )
        rows = cur.fetchall()
    get_db().commit()
    return [_row(r) for r in rows]


def get_owned_bank_account(user_id: int, bank_account_id: int) -> BankAccountRow:
    """The bank account, if it belongs to user_id and hasn't been removed; otherwise NotFound (R2.5)."""
    with get_db().cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM BankAccount WHERE bankAccountID = %s AND userID = %s AND is_active",
            (bank_account_id, user_id),
        )
        found = cur.fetchone()
    get_db().commit()
    if found is None:
        raise NotFound()
    return _row(found)

def remove_bank_account(user_id: int, bank_account_id: int) -> BankAccountRow:
    """Remove one of the customer's bank accounts (R10.6-R10.8, D25). It stays in past transfers."""
    with transaction() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM BankAccount WHERE bankAccountID = %s AND userID = %s AND is_active FOR UPDATE",
            (bank_account_id, user_id),
        )
        found = cur.fetchone()
        if found is None:
            raise NotFound()  # someone else's, already removed, or no such account (R10.8)
        cur.execute("UPDATE BankAccount SET is_active = FALSE WHERE bankAccountID = %s", (bank_account_id,))
    return _row(found)
