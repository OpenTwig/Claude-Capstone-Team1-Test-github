"""Stored XSS (task 5.4; N2.2): script payloads saved in every text field that is shown later must be
rendered escaped on every page that shows them, and the Content Security Policy blocks inline scripts.

The payloads go in through the real forms: an administrator's name, a customer's name, a bank account's
nickname and bank name, a stock's name and description, and a portfolio's name. Then every page is read
as each role.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.security import CONTENT_SECURITY_POLICY
from tests.factories import make_stock
from tests.web import csrf_token, login, make_login_user

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("America/Phoenix"))
PASSWORD = "correct horse battery"

SCRIPT = "<script>alert(1)</script>"
IMG = '"><img src=x onerror=alert(1)>'
# What each looks like when escaped correctly by Jinja.
ESCAPED = {SCRIPT: "&lt;script&gt;alert(1)&lt;/script&gt;", IMG: "&#34;&gt;&lt;img src=x onerror=alert(1)&gt;"}


@pytest.mark.parametrize("payload", [SCRIPT, IMG], ids=["script", "img-onerror"])
def test_stored_payloads_are_escaped_everywhere(db, web_app, fixed_clock, payload):  # N2.2
    fixed_clock(NOW)
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test", name=f"Ada {payload}")
    admin = web_app.test_client()
    login(admin, "admin@example.test", PASSWORD)

    # A customer named with the payload, created on the Administration page, then signed in.
    admin.post("/admin/users", data={"name": f"Casey {payload}", "ssn": "123-45-6789", "email": "casey@example.test",
                                     "date_of_birth": "01/15/1990", "role": "Customer", "csrf_token": csrf_token(admin, "/admin")})
    with db.cursor() as cur:
        cur.execute("UPDATE `User` SET password_hash = (SELECT h FROM (SELECT password_hash AS h FROM `User`"
                    " WHERE email = 'admin@example.test') x), must_change_password = FALSE WHERE email = 'casey@example.test'")
    db.commit()
    # A stock whose name and description are the payload (the description is limited to 50 characters).
    admin.post("/admin/stocks", data={"company_name": f"Evil {payload}", "symbol": "EVIL", "price": "10.00", "shares": "1000",
                                      "exchange": "", "description": payload, "csrf_token": csrf_token(admin, "/admin")})
    make_stock(db, symbol="AURA", price=Decimal("5.00"))

    customer = web_app.test_client()
    login(customer, "casey@example.test", PASSWORD)
    token = lambda: csrf_token(customer, "/account")  # noqa: E731
    customer.post("/account/portfolios", data={"account_name": f"Port {payload}", "account_type": "Brokerage", "csrf_token": token()})
    customer.post("/transfer/bank-accounts", data={"bank_name": f"Bank {payload}", "account_number": "000123456789",
                                                   "routing_number": "021000021", "nickname": f"Nick {payload}", "csrf_token": token()})
    with db.cursor() as cur:
        cur.execute("SELECT p.portfolioID, b.bankAccountID FROM Portfolio p JOIN BankAccount b ON b.userID = p.userID")
        ids = cur.fetchone()
    db.commit()
    assert ids, "the payloads were refused, so nothing is being tested"
    portfolio, bank = ids["portfolioID"], ids["bankAccountID"]
    customer.post("/transfer", data={"from_account": f"bank:{bank}", "to_account": f"portfolio:{portfolio}", "amount": "500.00",
                                     "transfer_date": "2026-09-29", "csrf_token": token()})
    review = customer.post("/trade/preview", data={"portfolio": portfolio, "symbol": "EVIL", "action": "Buy", "unit": "Dollars",
                                                   "amount": "20", "order_type": "Market", "timing": "Day Only",
                                                   "csrf_token": csrf_token(customer, "/trade")})
    nonce = review.get_data(as_text=True).split('name="nonce" value="')[1].split('"')[0]
    filled = customer.post("/trade/confirm", data={"nonce": nonce, "csrf_token": csrf_token(customer, "/trade")})

    pages = {
        "customer": [(p, customer.get(p)) for p in (
            "/account", f"/positions?portfolio={portfolio}", "/trade?symbol=EVIL", "/transfer", f"/history?portfolio={portfolio}",
            f"/history?portfolio={portfolio}&print=1", "/stocks", "/stocks?q=evil", "/stocks/EVIL")],
        "admin": [(p, admin.get(p)) for p in ("/admin", "/history", f"/history?portfolio={portfolio}", "/stocks", "/stocks/EVIL")],
    }
    pages["customer"] += [("review", review), ("filled", filled)]

    shown = 0
    for role, responses in pages.items():
        for path, response in responses:
            html = response.get_data(as_text=True)
            assert payload not in html, f"{role} {path}: the payload is in the page unescaped"
            assert "<script>alert" not in html and "onerror=alert(1)>" not in html.replace(ESCAPED[payload], "")
            shown += html.count(ESCAPED[payload])
            assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert shown >= 25, f"the payloads were only shown {shown} times: some fields may not have been saved"

    # The quote API is JSON (the page puts it in with textContent, never as HTML).
    api = customer.get(f"/api/quote/EVIL?portfolio={portfolio}")
    assert api.mimetype == "application/json" and api.get_json()["company_name"] == f"Evil {payload}"


def test_the_csp_forbids_inline_scripts():  # N2.2: even an unescaped script tag would not run
    rules = dict(part.strip().split(" ", 1) for part in CONTENT_SECURITY_POLICY.split(";"))
    assert rules["script-src"] == "'self'" and "'unsafe-inline'" not in CONTENT_SECURITY_POLICY
    assert rules["object-src"] == "'none'" and rules["frame-ancestors"] == "'none'"
