"""Boundary tests for every form field (task 5.4; R3.1-R3.3, R4.2-R4.3, R10.1-R10.2, R11.3, R12.2, R14.2-R14.3,
R15.5, R18.6, N2.2).

Each case is one form, one field, one value, and whether it must be accepted. A refused value must show an
error next to that field and change nothing in the database; an accepted value must be saved (or, for the
trade ticket, reach the review screen). Every other field in the form holds a valid value.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.crypto import encrypt_field
from app.migrate import APP_PRIVILEGES
from tests.factories import make_bank_account, make_portfolio, make_stock
from tests.web import csrf_token, login, make_login_user

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("America/Phoenix"))
PASSWORD = "correct horse battery"


def text_cases(maximum: int, minimum: int = 1, required: bool = True):
    """Empty, spaces only, the shortest and longest allowed, and one over the limit."""
    cases = [("", not required), ("   ", not required), ("a" * minimum, True), ("a" * maximum, True), ("a" * (maximum + 1), False)]
    return cases


MONEY_CASES = [
    ("0", False), ("0.00", False), ("-1", False), ("-0.01", False), ("1.001", False), ("abc", False), ("1e3", False),
    ("NaN", False), ("Infinity", False), ("-Infinity", False), ("", False), ("   ", False), ("$5", False),
    ("100000000.00", False),          # one cent over the column's 99,999,999.99
    ("0.01", True), ("1,000", True), ("250.50", True),
]
SHARES_CASES = [
    ("0", False), ("-1", False), ("1.0000001", False), ("abc", False), ("1e3", False), ("NaN", False),
    ("Infinity", False), ("", False), ("1000000000000", False),   # above 999,999,999,999.999999
    ("0.000001", True), ("2.5", True), ("1,000", True),
]
DOB_CASES = [
    ("01/15/1990", True), ("", False), ("   ", False), ("02/30/1990", False), ("13/01/1990", False),
    ("1990-01-15", False), ("01/15/2030", False),   # not in the past
    ("09/29/2026", False),                          # today is not in the past
    ("01/01/1899", False), ("abc", False),
]

# form -> (who, url, valid data, error-marker template, table a success adds a row to (None: check the page))
FORMS = {
    "add_user": ("admin", "/admin/users",
                 {"name": "Casey Customer", "ssn": "123-45-6789", "email": "casey@example.test", "date_of_birth": "01/15/1990",
                  "role": "Customer", "access_level": "", "department": ""},
                 'data-error="add_user:{field}"', "User"),
    "add_admin": ("admin", "/admin/users",
                  {"name": "Sam Support", "ssn": "123-45-6789", "email": "sam@example.test", "date_of_birth": "01/15/1990",
                   "role": "Administrator", "access_level": "support", "department": "Help Desk"},
                  'data-error="add_user:{field}"', "Admin"),
    "add_stock": ("admin", "/admin/stocks",
                  {"company_name": "New Company", "symbol": "NEWCO", "price": "42.18", "shares": "1000",
                   "exchange": "NASDAQ", "description": "Solar."},
                  'data-error="add_stock:{field}"', "Issuer"),
    "schedule": ("admin", "/admin/market-schedule",
                 {"open_time": "08:30", "close_time": "16:00", "trading_days": ["0", "1", "2", "3", "4"], "force_open": ""},
                 'data-error="schedule:{field}"', "MarketSchedule"),
    "portfolio": ("customer", "/account/portfolios", {"account_name": "Retirement", "account_type": "Brokerage"},
                  'data-error="{field}"', "Portfolio"),
    "bank": ("customer", "/transfer/bank-accounts",
             {"bank_name": "First Bank", "account_number": "000123456789", "routing_number": "021000021", "nickname": "Checking"},
             'data-error="{field}"', "BankAccount"),
    "deposit": ("customer", "/transfer",
                {"from_account": "bank:{bank}", "to_account": "portfolio:{empty}", "amount": "100.00", "transfer_date": "2026-09-29"},
                'data-error="{field}"', "Transfer"),
    "trade": ("customer", "/trade/preview",
              {"portfolio": "{portfolio}", "symbol": "AURA", "action": "Buy", "unit": "Dollars", "amount": "100.00",
               "order_type": "Market", "timing": "Day Only"},
              'data-error="{field}"', None),
    "trade_shares": ("customer", "/trade/preview",
                     {"portfolio": "{portfolio}", "symbol": "AURA", "action": "Buy", "unit": "Shares", "amount": "1",
                      "order_type": "Market", "timing": "Day Only"},
                     'data-error="{field}"', None),
}

CASES = {
    "add_user": {
        "name": text_cases(255),
        "ssn": [("123-45-6789", True), ("", False), ("   ", False), ("123456789", False), ("12-345-6789", False),
                ("123-45-678", False), ("abc-de-fghi", False), ("123-45-67890", False)],
        "email": [("casey@example.test", True), ("CASEY@Example.TEST", True), ("", False), ("   ", False),
                  ("not-an-email", False), ("casey@", False), ("@example.test", False), ("casey@example", False),
                  ("a" * 64 + "@example.test", True), ("a" * 65 + "@example.test", False),          # 64 before the @
                  ("a@" + "b" * 250 + ".test", False)],                                           # over 254 in all
        "date_of_birth": DOB_CASES,
        "role": [("Customer", True), ("", False), ("Owner", False)],
    },
    "add_admin": {
        "access_level": text_cases(20),
        "department": text_cases(100, required=False),
    },
    "add_stock": {
        "company_name": text_cases(255),
        "symbol": [("A", True), ("ABCDEFGHIJ", True), ("ab12", True), ("", False), ("   ", False), ("ABCDEFGHIJK", False),
                   ("AB-C", False), ("AB C", False), ("ÅB", False)],
        "price": MONEY_CASES + [("99999999.99", True)],
        "shares": SHARES_CASES + [("999999999999.999999", True)],
        "description": text_cases(50, required=False),
        "exchange": text_cases(10, required=False),
    },
    "schedule": {
        "open_time": [("08:30", True), ("", False), ("25:00", False), ("8am", False)],   # open >= close: see close_time
        "close_time": [("16:00", True), ("", False), ("24:00", False), ("08:30", False), ("08:00", False)],
        # A list is several checked boxes; [] is none checked (R15.3, R15.5, D29).
        "trading_days": [(["0", "1", "2", "3", "4"], True), (["5", "6"], True), ("0", True), ("6", True), ([], False),
                         ("7", False), ("-1", False), ("Monday", False), ("", False), (["0", "9"], False)],
        "force_open": [("on", True), ("", True)],   # R15.7: anything but "on" leaves it off
    },
    "portfolio": {
        "account_name": text_cases(100),
        "account_type": [("Brokerage", True), ("", False), ("Margin", False)],
    },
    "bank": {
        "bank_name": text_cases(100),
        "nickname": text_cases(100),
        "account_number": [("1234", True), ("1" * 17, True), ("123", False), ("1" * 18, False), ("", False),
                           ("12a4", False), ("1234-5678", False), ("  ", False)],
        "routing_number": [("021000021", True), ("02100002", False), ("0210000210", False), ("", False),
                           ("02100002a", False), ("021-000-021", False)],
    },
    "deposit": {
        "amount": MONEY_CASES + [("99999999.99", True)],
        "transfer_date": [("2026-09-29", True), ("2026-09-28", False), ("2026-09-30", False), ("", False),
                          ("2026-13-01", False), ("09/29/2026", False)],
    },
    "trade": {
        "amount": [c for c in MONEY_CASES if c[0] not in ("250.50",)],
        "action": [("Buy", True), ("", False), ("Short", False)],
        "unit": [("Dollars", True), ("", True), ("Euros", False)],      # an empty unit means Dollars
    },
    "trade_shares": {
        # At $10.00 a share, 0.000001 costs $0.00001: under a cent, so it is refused; 0.001 costs exactly $0.01.
        "amount": [c for c in SHARES_CASES if c[0] not in ("1,000", "0.000001")] + [("0.000001", False), ("0.001", True)],
    },
}

PARAMS = [(form, field, value, ok) for form, fields in CASES.items() for field, cases in fields.items()
          for value, ok in cases]


def case_id(p):
    form, field, value, ok = p
    shown = value if len(value) <= 12 else f"{value[:6]}…[{len(value)}]"
    return f"{form}-{field}-{shown!r}-{'ok' if ok else 'refused'}"


@pytest.fixture
def world(db, web_app, fixed_clock):
    fixed_clock(NOW)
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    customer = make_login_user(db, PASSWORD, email="c@example.test")
    portfolio = make_portfolio(db, customer, account_name="Trading", cash_balance=Decimal("50000.00"))   # money to trade
    empty = make_portfolio(db, customer, account_name="Empty", cash_balance=Decimal("0.00"))  # room for the largest deposit
    with web_app.app_context():
        bank = make_bank_account(db, customer, account_number=encrypt_field("000999888777"))
    make_stock(db, symbol="AURA", price=Decimal("10.00"), shares_available=Decimal("999999999999"))
    return {"portfolio": portfolio, "empty": empty, "bank": bank}


def counts(db):
    with db.cursor() as cur:
        found = {}
        for table in APP_PRIVILEGES:
            cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
            found[table] = cur.fetchone()["n"]
    db.commit()
    return found


@pytest.mark.parametrize("form, field, value, ok", PARAMS, ids=[case_id(p) for p in PARAMS])
def test_form_field_boundaries(db, web_app, world, form, field, value, ok):  # N2.2
    who, url, valid, marker, table = FORMS[form]
    client = web_app.test_client()
    login(client, "admin@example.test" if who == "admin" else "c@example.test", PASSWORD)
    data = {k: v.format(**world) if isinstance(v, str) else v for k, v in valid.items()}
    data[field] = value
    data["csrf_token"] = csrf_token(client, "/admin" if who == "admin" else "/account")
    before = counts(db)
    response = client.post(url, data=data)
    assert response.status_code < 500
    html = response.get_data(as_text=True)
    error_shown = marker.format(field=field) in html
    after = counts(db)
    if ok:
        assert not error_shown, f"{field}={value!r} was refused but should be accepted"
        if table:
            assert after[table] == before[table] + 1, f"{field}={value!r} was accepted but nothing was saved"
        else:
            assert "Review Your Order" in html
    else:
        assert error_shown, f"{field}={value!r} was not refused with an error next to the field"
        assert after == before, f"{field}={value!r} was refused but something changed"


# --- Change password (R18.6, N1.6) --------------------------------------------------------------------------

@pytest.mark.parametrize("new, ok", [
    ("Jumping-Otter-42", True),
    ("Otter-Jump-4", True),                 # exactly 12 characters
    ("Otter-Jump4", False),                 # 11
    ("x" * 129, False),                     # over 128
    ("aaaaaaaaaaaaaa", False),              # too few different characters
    ("password1234", False),                # too common
    ("            ", False),                # spaces only
    ("", False),
    ("new.user-otter-99", False),           # contains the email name
])
def test_new_password_boundaries(db, web_app, fixed_clock, new, ok):  # R18.6, N1.6
    fixed_clock(NOW)
    user = make_login_user(db, PASSWORD, email="new.user@example.test")
    with db.cursor() as cur:
        cur.execute("UPDATE `User` SET must_change_password = TRUE WHERE userID = %s", (user,))
    db.commit()
    client = web_app.test_client()
    login(client, "new.user@example.test", PASSWORD)
    response = client.post("/change-password", data={"new_password": new, "confirm_password": new,
                                                     "csrf_token": csrf_token(client, "/change-password")})
    with db.cursor() as cur:
        cur.execute("SELECT must_change_password FROM `User` WHERE userID = %s", (user,))
        still_required = cur.fetchone()["must_change_password"]
    db.commit()
    if ok:
        assert response.status_code == 302 and not still_required
    else:
        assert 'data-error="new_password"' in response.get_data(as_text=True) and still_required
