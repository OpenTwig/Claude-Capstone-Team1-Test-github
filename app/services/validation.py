"""Field validation shared by the services (N2.2: all input is validated on the server).

Each function returns the cleaned value or raises ValidationFailed naming the
field, so forms can show the message next to that field.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from app.errors import ValidationFailed

# Email syntax (RFC 5321/5322 "dot-atom" form, ASCII only):
#   local part: allowed characters, dots only between characters (no leading, trailing, or double dots)
#   domain: labels of letters/digits/hyphens, not starting or ending with a hyphen, then a real-looking
#   top-level domain (letters, or an internationalised "xn--" one)
_ATEXT = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_TLD = r"(?:[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{1,59})"
EMAIL = re.compile(rf"{_ATEXT}+(?:\.{_ATEXT}+)*@(?:{_LABEL}\.)+{_TLD}")
EMAIL_MAX = 254       # longest deliverable address (RFC 5321)
EMAIL_LOCAL_MAX = 64  # longest part before the @ (RFC 5321)

# Password policy for any password a person chooses (N1.6). Follows NIST SP 800-63B: length and a
# block-list rather than "must contain a symbol" rules. Temporary passwords are generated, not chosen.
PASSWORD_MIN = 12
PASSWORD_MAX = 128   # bounds the work of hashing, so huge inputs can't slow the server
PASSWORD_MIN_DISTINCT = 5
COMMON_PASSWORDS = frozenset(
    {
        "123456789012", "1234567890123", "12345678901234", "123456123456", "111111111111",
        "000000000000", "password1234", "password12345", "password123!", "passwordpassword",
        "qwertyuiop12", "qwertyuiop123", "qwerty123456", "1q2w3e4r5t6y", "iloveyou1234",
        "abc123abc123", "letmein12345", "welcome12345", "admin1234567", "administrator",
        "changeme1234", "trustno11234", "football1234", "baseball1234", "monkey123456",
        "sunshine1234", "princess1234", "dragon123456", "starwars1234", "superman1234",
        "correcthorsebatterystaple", "stocktrading1", "stocktrading123", "tradingaccount",
    }
)

# [0-9], not \d: in Python \d also matches other scripts' digits (e.g. Arabic-Indic), which the
# database's CHECK constraints reject, so they would get past validation and fail as a 500 error.
SSN = re.compile(r"[0-9]{3}-[0-9]{2}-[0-9]{4}")
ROUTING_NUMBER = re.compile(r"[0-9]{9}")
BANK_ACCOUNT_NUMBER = re.compile(r"[0-9]{4,17}")  # US bank account numbers are 4-17 digits


def text(field: str, value: str | None, label: str, max_length: int, required: bool = True) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        if required:
            raise ValidationFailed(field, f"{label} is required.")
        return None
    if len(cleaned) > max_length:
        raise ValidationFailed(field, f"{label} must be {max_length} characters or fewer.")
    return cleaned


def email(value: str | None, field: str = "email") -> str:
    """A well-formed email address, lower-cased (R3.1).

    This checks the format only. Proving the address is real and belongs to the person needs a
    confirmation email, which the system doesn't send (D2); add that before any self-registration.
    """
    cleaned = text(field, value, "Email", EMAIL_MAX).lower()
    local = cleaned.rsplit("@", 1)[0]
    if not EMAIL.fullmatch(cleaned) or len(local) > EMAIL_LOCAL_MAX:
        raise ValidationFailed(field, "Enter a valid email address, like name@example.com.")
    return cleaned


def password(value: str | None, email_address: str | None = None, field: str = "password") -> str:
    """A password a person has chosen (N1.6). Not trimmed: spaces are allowed and count."""
    value = value or ""
    if not value.strip():
        raise ValidationFailed(field, "Password is required.")
    if len(value) < PASSWORD_MIN:
        raise ValidationFailed(field, f"Password must be at least {PASSWORD_MIN} characters.")
    if len(value) > PASSWORD_MAX:
        raise ValidationFailed(field, f"Password must be {PASSWORD_MAX} characters or fewer.")
    lowered = value.lower()
    if lowered in COMMON_PASSWORDS or len(set(lowered)) < PASSWORD_MIN_DISTINCT:
        raise ValidationFailed(field, "That password is too common or too simple. Choose something harder to guess.")
    if email_address:
        name = email_address.split("@", 1)[0].lower()
        if len(name) >= 3 and name in lowered:
            raise ValidationFailed(field, "Password must not contain your email name.")
    return value


def ssn(value: str | None, field: str = "ssn") -> str:
    cleaned = (value or "").strip()
    if not SSN.fullmatch(cleaned):
        raise ValidationFailed(field, "Enter the SSN as XXX-XX-XXXX.")
    return cleaned


def us_date(text_value: str | None, field: str, label: str) -> date:
    """Parse MM/DD/YYYY, the format the wireframes use."""
    try:
        return datetime.strptime((text_value or "").strip(), "%m/%d/%Y").date()
    except ValueError as exc:
        raise ValidationFailed(field, f"Enter {label} as MM/DD/YYYY.") from exc


def date_of_birth(value: date | None, today: date, field: str = "date_of_birth") -> date:
    if value is None:
        raise ValidationFailed(field, "Date of birth is required.")
    if value >= today:
        raise ValidationFailed(field, "Date of birth must be in the past.")
    if value.year < 1900:
        raise ValidationFailed(field, "Enter a date of birth after 1900.")
    return value


def routing_number(value: str | None, field: str = "routing_number") -> str:
    cleaned = (value or "").strip()
    if not ROUTING_NUMBER.fullmatch(cleaned):
        raise ValidationFailed(field, "Routing number must be exactly 9 digits.")
    return cleaned


def bank_account_number(value: str | None, field: str = "account_number") -> str:
    cleaned = (value or "").strip()
    if not BANK_ACCOUNT_NUMBER.fullmatch(cleaned):
        raise ValidationFailed(field, "Account number must be 4 to 17 digits.")
    return cleaned
