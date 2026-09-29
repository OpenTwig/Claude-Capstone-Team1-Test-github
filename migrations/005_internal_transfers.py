"""Transfers between a customer's own brokerage accounts (DV8, D15, R19).

A Python migration because the Transfer "shape" CHECK from 001 has a name MySQL
generated: transfer_chk_3 where table names are stored in lower case (Windows),
Transfer_chk_3 elsewhere. It is found by its clause instead of its name.
"""
import re

SAFE_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def _value(row, key):
    return row[key] if isinstance(row, dict) else row[0]


def upgrade(cursor):
    cursor.execute(
        "SELECT cc.CONSTRAINT_NAME AS name"
        " FROM information_schema.CHECK_CONSTRAINTS cc"
        " JOIN information_schema.TABLE_CONSTRAINTS tc"
        "   ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME"
        " WHERE cc.CONSTRAINT_SCHEMA = DATABASE() AND LOWER(tc.TABLE_NAME) = 'transfer'"
        "   AND tc.CONSTRAINT_TYPE = 'CHECK' AND cc.CHECK_CLAUSE LIKE %s",
        ("%Withdrawal%",),
    )
    names = [_value(row, "name") for row in cursor.fetchall()]
    if len(names) != 1 or not SAFE_NAME.fullmatch(names[0]):
        raise RuntimeError(f"expected exactly one Transfer shape CHECK constraint, found {names!r}")

    cursor.execute(f"ALTER TABLE Transfer DROP CHECK `{names[0]}`")
    cursor.execute(
        "ALTER TABLE Transfer ADD CONSTRAINT transfer_shape CHECK ("
        " (transfer_type = 'Deposit'    AND bankAccountID IS NOT NULL AND to_portfolioID IS NOT NULL"
        "                               AND from_portfolioID IS NULL) OR"
        " (transfer_type = 'Withdrawal' AND bankAccountID IS NOT NULL AND from_portfolioID IS NOT NULL"
        "                               AND to_portfolioID IS NULL) OR"
        " (transfer_type = 'Internal'   AND bankAccountID IS NULL AND from_portfolioID IS NOT NULL"
        "                               AND to_portfolioID IS NOT NULL AND from_portfolioID <> to_portfolioID))"
    )
    # An Internal transfer has one History row per account (R19.4): unique per (transfer, account)
    # instead of per transfer. The new index is added first so it can serve the foreign key.
    cursor.execute("ALTER TABLE History ADD UNIQUE KEY u_history_transfer_portfolio (transferID, portfolioID)")
    cursor.execute("ALTER TABLE History DROP INDEX transferID")
