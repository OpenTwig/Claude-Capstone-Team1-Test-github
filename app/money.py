"""Exact money and share-quantity helpers (design: Core modules, money.py).

All money is decimal.Decimal, never float. Money columns are DECIMAL(10,2);
share quantities are DECIMAL(18,6).
"""
from __future__ import annotations

import re
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
SHARE = Decimal("0.000001")
MAX_MONEY = Decimal("99999999.99")  # largest DECIMAL(10,2)
MAX_SHARES = Decimal("999999999999.999999")  # largest DECIMAL(18,6)

# The limits as users see them, so every "too large" message states the actual limit.
MAX_MONEY_TEXT = f"${MAX_MONEY:,}"      # $99,999,999.99
MAX_SHARES_TEXT = f"{MAX_SHARES:,}"     # 999,999,999,999.999999
TOO_MUCH_MONEY = f"The largest amount you can enter is {MAX_MONEY_TEXT}."
TOO_MANY_SHARES = f"The largest number of shares you can enter is {MAX_SHARES_TEXT}."

# ASCII digits only ([0-9], not \d, which also matches other scripts' digits).
# Whole part: plain digits, or digits grouped in threes with commas ("1,000", "25,000,000"; D27).
# Commas anywhere else ("1,00", "10,00", ",5") are refused, so an amount is never guessed.
_WHOLE = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"
_MONEY_TEXT = re.compile(_WHOLE + r"(\.[0-9]{1,2})?")
_SHARES_TEXT = re.compile(_WHOLE + r"(\.[0-9]{1,6})?")


def _require_decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"expected Decimal, got {type(value).__name__}; never use float for money")
    if not value.is_finite():
        raise ValueError(f"expected a finite amount, got {value}")
    return value


def to_cents(value: Decimal) -> Decimal:
    """Round to the cent, halves rounding up (away from zero)."""
    return _require_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def to_shares_floor(value: Decimal) -> Decimal:
    """Round a share quantity down to 6 decimal places (quantities derived from dollar amounts)."""
    return _require_decimal(value).quantize(SHARE, rounding=ROUND_DOWN)


def parse_money(text: str) -> Decimal:
    """Parse a user-entered dollar amount: digits, optional 2 decimals, greater than 0.

    Accepts thousands separators in the usual places ("1,000", "25,000.50"; D27). Rejects
    signs, misplaced commas, currency symbols, exponents, NaN, and Infinity. Raises
    ValueError with a message suitable for showing next to the form field.
    """
    cleaned = (text or "").strip()
    if not _MONEY_TEXT.fullmatch(cleaned):
        raise ValueError("Enter an amount like 250, 1,000, or 250.50 (at most 2 decimal places).")
    amount = Decimal(cleaned.replace(",", ""))
    if amount <= 0:
        raise ValueError("Enter an amount greater than $0.00.")
    if amount > MAX_MONEY:
        raise ValueError(TOO_MUCH_MONEY)
    return amount


def parse_shares(text: str) -> Decimal:
    """Parse a user-entered share quantity: digits, optional 6 decimals, greater than 0.

    Thousands separators are accepted in the usual places, as in parse_money (D27).
    """
    cleaned = (text or "").strip()
    if not _SHARES_TEXT.fullmatch(cleaned):
        raise ValueError("Enter a number of shares like 10, 1,000, or 2.5 (at most 6 decimal places).")
    quantity = Decimal(cleaned.replace(",", ""))
    if quantity <= 0:
        raise ValueError("Enter a number of shares greater than 0.")
    if quantity > MAX_SHARES:
        raise ValueError(TOO_MANY_SHARES)
    return quantity
