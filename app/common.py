"""Small helpers that several pages use.

    Time         now_utc(), now_for_database(), today()
    Money        to_cents(), percent(), money() and shares() for showing numbers
    Forms        check_text(), check_digits(), parse_money(), parse_shares()
    Encryption   encrypt(), decrypt()
    Portfolios   list_portfolios(), get_portfolio()
"""
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from flask import current_app

from app.models import Portfolio

ARIZONA = ZoneInfo("America/Phoenix")  # the market runs on Arizona time

ONE_CENT = Decimal("0.01")


class UserError(Exception):
    """A problem with what the user typed, to show them on the page.

    `field` says which form box the message belongs next to. "form" means the top of the page.

    We use it like this:

        try:
            amount = parse_money(request.form.get("amount"))
        except UserError as error:
            errors[error.field] = error.message
    """

    def __init__(self, message, field="form"):
        super().__init__(message)
        self.message = message
        self.field = field


# --- Time --------------------------------------------------------------------------------------------

def now_utc():
    """The current date and time in UTC."""
    return datetime.now(timezone.utc)


def now_for_database():
    """The current time as we store it in the database: UTC, without the time zone attached."""
    return now_utc().replace(tzinfo=None)


def today():
    """Today's date in Arizona."""
    return now_utc().astimezone(ARIZONA).date()


# --- Money -------------------------------------------------------------------------------------------

def to_cents(amount):
    """Round to the nearest cent: 10.005 -> 10.01."""
    return amount.quantize(ONE_CENT, rounding=ROUND_HALF_UP)


def percent(part, whole):
    """`part` as a percentage of `whole`, rounded to 2 places: percent(5, 200) -> 2.50.

    Returns 0.00 when `whole` is 0, because we can't divide by zero.
    """
    if whole <= 0:
        return Decimal("0.00")
    result = part / whole * 100
    return result.quantize(ONE_CENT, rounding=ROUND_HALF_UP)


def money(amount):
    """Show an amount of money: 1234.5 -> "$1,234.50", -3 -> "-$3.00". Used in templates as | money."""
    if amount is None:
        return "—"
    cents = to_cents(Decimal(amount))
    if cents < 0:
        return f"-${abs(cents):,.2f}"
    return f"${cents:,.2f}"


def shares(quantity):
    """Show a number of shares without extra zeros: 2.500000 -> "2.5". Used in templates as | shares."""
    if quantity is None:
        return "—"
    text = f"{Decimal(quantity):,.6f}"
    text = text.rstrip("0").rstrip(".")
    if text == "" or text == "-0":
        return "0"
    return text


# --- Checking form fields ----------------------------------------------------------------------------
# Each check_... or parse_... function returns the cleaned-up value, or raises UserError.

# A whole number, optionally with commas in the right places (1,000), and optional decimals.
NUMBER_PATTERN = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.([0-9]+))?")


def _parse_number(typed, decimal_places, field, example):
    """Turn typed text like "1,250.50" into a Decimal greater than 0."""
    cleaned = (typed or "").strip()
    match = NUMBER_PATTERN.fullmatch(cleaned)
    if match is None:
        raise UserError(f"Enter {example} (at most {decimal_places} decimal places).", field)

    decimals = match.group(1) or ""
    if len(decimals) > decimal_places:
        raise UserError(f"Enter {example} (at most {decimal_places} decimal places).", field)

    number = Decimal(cleaned.replace(",", ""))
    if number <= 0:
        raise UserError("Enter an amount greater than 0.", field)
    return number


def parse_money(typed, field="amount"):
    """A dollar amount, like 250 or 1,250.50."""
    return _parse_number(typed, 2, field, "an amount like 250 or 250.50")


def parse_shares(typed, field="amount"):
    """A number of shares, like 10 or 2.5 (fractions of a share are allowed)."""
    return _parse_number(typed, 6, field, "a number of shares like 10 or 2.5")


def check_text(typed, field, label, required=True):
    """Text such as a name. Returns None when an optional box is left empty.

    (The form boxes have a maxlength, so the browser stops anyone typing too much.)
    """
    cleaned = (typed or "").strip()
    if cleaned == "":
        if required:
            raise UserError(f"{label} is required.", field)
        return None
    return cleaned


def check_digits(typed, field, pattern, message):
    """Text that must match a pattern exactly, like an SSN or a routing number."""
    cleaned = (typed or "").strip()
    if not re.fullmatch(pattern, cleaned):
        raise UserError(message, field)
    return cleaned


# --- Encryption: SSNs and bank account numbers are stored encrypted ----------------------------------

def encrypt(plain_text):
    key = current_app.config["FIELD_ENCRYPTION_KEY"]
    return Fernet(key).encrypt(plain_text.encode()).decode()


def decrypt(encrypted_text):
    key = current_app.config["FIELD_ENCRYPTION_KEY"]
    return Fernet(key).decrypt(encrypted_text.encode()).decode()


# --- Portfolios --------------------------------------------------------------------------------------

def list_portfolios(user_id):
    """All of a customer's portfolios, oldest first."""
    query = Portfolio.query.filter_by(user_id=user_id)
    return query.order_by(Portfolio.created_at, Portfolio.portfolio_id).all()


def get_portfolio(user_id, portfolio_id):
    """One of the customer's portfolios. Shows the 404 page if it isn't theirs.

    We show "not found" rather than "not allowed", so nobody can tell that it exists.
    """
    query = Portfolio.query.filter_by(portfolio_id=portfolio_id, user_id=user_id)
    return query.first_or_404()


