"""Shared field validation (N2.2), including the properties that need no database."""
import re
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.errors import ValidationFailed
from app.services import validation


@given(st.text(max_size=12))
def test_p14_routing_number_accepted_iff_exactly_nine_ascii_digits(value):  # P14, R10.2
    expected = len(value.strip()) == 9 and all(c in "0123456789" for c in value.strip())
    try:
        validation.routing_number(value)
        accepted = True
    except ValidationFailed:
        accepted = False
    assert accepted == expected


@given(st.text(alphabet="0123456789٣３-", min_size=9, max_size=9))
def test_p14_mostly_digit_strings(value):
    """Focus the search on near-misses: nine characters drawn mostly from digits."""
    try:
        validation.routing_number(value)
        accepted = True
    except ValidationFailed:
        accepted = False
    assert accepted == bool(re.fullmatch(r"[0-9]{9}", value))


@pytest.mark.parametrize("value", ["٣٣٣٣٣٣٣٣٣", "１２３４５６７８９"])
def test_non_ascii_digits_are_rejected_before_they_reach_the_database(value):
    """The database CHECK only allows 0-9; anything else must fail validation, not the INSERT."""
    with pytest.raises(ValidationFailed):
        validation.routing_number(value)
    with pytest.raises(ValidationFailed):
        validation.bank_account_number(value)
    with pytest.raises(ValidationFailed):
        validation.ssn("٣٣٣-٣٣-٣٣٣٣")


@pytest.mark.parametrize("value, ok", [("1234", True), ("12345678901234567", True), ("123", False),
                                       ("123456789012345678", False), ("12a4", False), ("", False)])
def test_bank_account_number_length(value, ok):  # R10.1
    if ok:
        assert validation.bank_account_number(value) == value
    else:
        with pytest.raises(ValidationFailed):
            validation.bank_account_number(value)


@pytest.mark.parametrize("text, expected", [("05/17/1990", date(1990, 5, 17)), (" 12/31/2000 ", date(2000, 12, 31))])
def test_us_dates(text, expected):
    assert validation.us_date(text, "d", "the date") == expected


@pytest.mark.parametrize("text", ["1990-05-17", "02/30/2026", "13/01/2000", "", "5/17", None])
def test_invalid_us_dates(text):
    with pytest.raises(ValidationFailed, match="MM/DD/YYYY"):
        validation.us_date(text, "d", "the date")


# --- Email (R3.1) ------------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("a@b.co", "a@b.co"),
    ("First.Last+tag@Sub.Example.COM", "first.last+tag@sub.example.com"),
    ("o'brien@example.ie", "o'brien@example.ie"),
    ("  casey@example.test  ", "casey@example.test"),
    ("casey@example.test\n", "casey@example.test"),  # surrounding whitespace, even a newline, is trimmed
    ("user_name-1@my-host.example.org", "user_name-1@my-host.example.org"),
    ("x@example.xn--p1ai", "x@example.xn--p1ai"),  # internationalised top-level domain
])
def test_valid_emails_are_accepted_and_lower_cased(value, expected):
    assert validation.email(value) == expected


@pytest.mark.parametrize("value", [
    "", "plainaddress", "@example.com", "name@", "name@example", "name@example.c",
    "a..b@example.com", ".a@example.com", "a.@example.com", "a@-example.com", "a@example-.com",
    "a@example..com", "a@.example.com", "a@example.com.", "a b@example.com", "a@exa mple.com",
    "a@b@example.com", "<script>@example.com", "a(b)@example.com", "a,b@example.com",
    "ünïcode@example.com", "name@exämple.com", "name@123.456", "name@example.c0m",
    "x" * 65 + "@example.com",                   # local part over 64 characters
    "a@" + "b" * 250 + ".com",                   # over 254 characters in total
    "name\n@example.com", "name@example.com\nBcc: other@example.com",  # line breaks inside
    "name@example.com; DROP TABLE `User`",
])
def test_non_emails_are_rejected(value):
    with pytest.raises(ValidationFailed) as exc:
        validation.email(value)
    assert exc.value.field == "email"


@given(st.emails())
def test_standard_email_addresses_are_accepted(address):
    """Hypothesis generates RFC-valid addresses; any that fit the length limits must pass."""
    local = address.rsplit("@", 1)[0]
    if len(address) <= validation.EMAIL_MAX and len(local) <= validation.EMAIL_LOCAL_MAX:
        assert validation.email(address) == address.lower()


# --- Password policy (N1.6) ---------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "a-long-password", "correct horse battery", "Tr4d1ng!Desk#2026", "x" * 11 + "yzabc", "  spaces count  ",
])
def test_good_passwords_are_accepted(value):
    assert validation.password(value, "casey@example.test") == value


@pytest.mark.parametrize("value, message", [
    ("", "required"),
    ("            ", "required"),
    ("short", "at least 12"),
    ("elevenchars", "at least 12"),
    ("x" * 129, "128 characters or fewer"),
    ("password1234", "too common"),
    ("PASSWORD1234", "too common"),        # the block-list ignores case
    ("123456789012", "too common"),
    ("aaaaaaaaaaaa", "too common"),         # too few different characters
    ("abababababab", "too common"),
    ("casey-is-great-2026", "email name"),  # contains the email's name part
])
def test_weak_passwords_are_rejected(value, message):
    with pytest.raises(ValidationFailed) as exc:
        validation.password(value, "Casey@example.test")
    assert exc.value.field == "password" and message in exc.value.message


def test_short_email_names_are_not_treated_as_password_words():
    assert validation.password("a-long-password", "al@example.test")


def test_text_fields_are_trimmed_and_length_limited():
    assert validation.text("f", "  hi  ", "Field", 5) == "hi"
    assert validation.text("f", "   ", "Field", 5, required=False) is None
    with pytest.raises(ValidationFailed, match="Field is required"):
        validation.text("f", None, "Field", 5)
    with pytest.raises(ValidationFailed, match="5 characters or fewer"):
        validation.text("f", "123456", "Field", 5)
