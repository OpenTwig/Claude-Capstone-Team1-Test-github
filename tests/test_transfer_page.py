"""Transfer Funds page (task 3.1; wf-7; R10-R12, R19, R2.5, N2.3, D9, D13, D15, D24)."""
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app import create_app
from app.crypto import mask_last4
from app.filters import money
from app.services import bank_accounts
from tests.factories import make_portfolio, make_user
from tests.web import csrf_token, login, make_login_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=AZ)
PASSWORD = "correct horse battery"


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


@pytest.fixture
def customer(db, client, fixed_clock):
    fixed_clock(NOW)
    user = make_login_user(db, PASSWORD, email="casey@example.test")
    login(client, "casey@example.test", PASSWORD)
    return user


@pytest.fixture
def funded(db, svc, customer):
    """The customer with a bank account and a portfolio holding $500.00."""
    bank = svc(bank_accounts.add_bank_account, customer, "First Bank", "000123456789", "021000021", "Checking")
    portfolio = make_portfolio(db, customer, account_name="Brokerage", cash_balance=D("500.00"))
    return bank.bank_account_id, portfolio


def post_transfer(client, source, target, amount, day="2026-09-28"):
    return client.post("/transfer", data={
        "from_account": source, "to_account": target, "amount": amount, "transfer_date": day,
        "csrf_token": csrf_token(client, "/transfer"),
    })


def cash(db, portfolio_id):
    return one(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (portfolio_id,))["cash_balance"]


# --- The page (wf-7) -----------------------------------------------------------------------------

def test_page_shows_the_transfer_and_bank_forms(client, funded):
    html = client.get("/transfer").get_data(as_text=True)
    for text in ("Transfer Funds", ">From<", ">To<", "Cash Amount", "Transfer Date", "Add Bank Account",
                 "Bank Name", "Account Number", "Routing Number", "Nickname", "Support"):
        assert text in html, text
    assert 'value="2026-09-28"' in html                      # today's Arizona date, filled in (A5)
    assert 'data-name="Checking" data-detail="First Bank"' in html  # From/To show the name, no number (D25)
    assert "First Bank •••• 6789" in html                     # the bank list shows it masked (R10.5)
    assert "000123456789" not in html                        # never the full number
    assert "Brokerage" in html and "$500.00" in html
    assert '<script src="/static/js/transfer.js"></script>' in html


def test_no_bank_account_disables_transfers_with_the_message(client, customer):  # R11.6
    html = client.get("/transfer").get_data(as_text=True)
    assert "Link a bank account before transferring funds" in html
    assert re.search(r"<fieldset[^>]*disabled", html)
    assert 'id="add-bank" class' not in html and 'class="collapse show" id="add-bank"' in html  # bank form open


def test_no_portfolio_disables_transfers(db, svc, client, customer):
    svc(bank_accounts.add_bank_account, customer, "Bank", "11112222", "021000021", "Main")
    html = client.get("/transfer").get_data(as_text=True)
    assert "You don't have a portfolio yet." in html and re.search(r"<fieldset[^>]*disabled", html)


# --- Deposit and withdraw through the page ------------------------------------------------------------

def test_deposit_through_the_page(db, client, funded):  # R11.4, R11.5
    bank, portfolio = funded
    response = post_transfer(client, f"bank:{bank}", f"portfolio:{portfolio}", "250.25")
    assert response.status_code == 302 and response.headers["Location"] == "/transfer"
    assert cash(db, portfolio) == D("750.25")
    assert "Deposited $250.25 into Brokerage. New cash balance: $750.25." in client.get("/transfer").get_data(as_text=True)


def test_amounts_can_have_thousands_separators(db, client, funded):  # D27
    bank, portfolio = funded
    assert post_transfer(client, f"bank:{bank}", f"portfolio:{portfolio}", "1,000.25").status_code == 302
    assert cash(db, portfolio) == D("1500.25")


def test_withdraw_through_the_page(db, client, funded):  # R12.4, R12.5
    bank, portfolio = funded
    post_transfer(client, f"portfolio:{portfolio}", f"bank:{bank}", "100")
    assert cash(db, portfolio) == D("400.00")
    assert "Withdrew $100.00 from Brokerage. New cash balance: $400.00." in client.get("/transfer").get_data(as_text=True)


def test_insufficient_funds_is_shown_and_nothing_changes(db, client, funded):  # R12.3
    bank, portfolio = funded
    html = post_transfer(client, f"portfolio:{portfolio}", f"bank:{bank}", "500.01").get_data(as_text=True)
    assert 'data-error="form">Insufficient funds<' in html
    assert cash(db, portfolio) == D("500.00")


@pytest.mark.parametrize("amount, message", [
    ("", "at most 2 decimal places"), ("0", "greater than $0.00"), ("-5", "at most 2 decimal places"),
    ("abc", "at most 2 decimal places"), ("1.001", "at most 2 decimal places"), ("1e3", "at most 2 decimal places"),
])
def test_bad_amounts_show_a_field_error(db, client, funded, amount, message):  # R11.3, N2.2
    bank, portfolio = funded
    html = post_transfer(client, f"bank:{bank}", f"portfolio:{portfolio}", amount).get_data(as_text=True)
    assert 'data-error="amount"' in html and message in html
    assert cash(db, portfolio) == D("500.00")


def test_a_date_other_than_today_is_refused(db, client, funded):  # A5
    bank, portfolio = funded
    html = post_transfer(client, f"bank:{bank}", f"portfolio:{portfolio}", "10", day="2026-09-29").get_data(as_text=True)
    assert 'data-error="transfer_date"' in html and "must be today (09/28/2026)" in html


def test_bank_to_bank_is_refused(db, svc, client, customer, funded):  # D15: only portfolio-to-portfolio is new
    bank, portfolio = funded
    second_bank = svc(bank_accounts.add_bank_account, customer, "Other", "55556666", "021000021", "Savings").bank_account_id
    html = post_transfer(client, f"bank:{bank}", f"bank:{second_bank}", "10").get_data(as_text=True)
    assert 'data-error="to_account"' in html and "can&#39;t move from one bank account to another" in html
    assert cash(db, portfolio) == D("500.00")


# --- Moves between the customer's own brokerage accounts (R19, D15) --------------------------------

def label(db, portfolio_id):
    found = one(db, "SELECT account_name, account_number FROM Portfolio WHERE portfolioID = %s", (portfolio_id,))
    return f"{found['account_name']} {mask_last4(found['account_number'])}"


def test_move_between_own_accounts_through_the_page(db, client, customer, funded):  # R19.1, R19.3, R19.5
    bank, portfolio = funded
    savings = make_portfolio(db, customer, account_name="Brokerage", cash_balance=D("20.00"))  # same name on purpose
    response = post_transfer(client, f"portfolio:{portfolio}", f"portfolio:{savings}", "125.50")
    assert response.status_code == 302 and response.headers["Location"] == "/transfer"
    assert (cash(db, portfolio), cash(db, savings)) == (D("374.50"), D("145.50"))
    html = client.get("/transfer").get_data(as_text=True)
    assert (f"Moved $125.50 from {label(db, portfolio)} (new cash balance $374.50) to {label(db, savings)} "
            f"(new cash balance $145.50).") in html
    assert label(db, portfolio) != label(db, savings)  # the masked numbers tell same-named accounts apart


def test_moves_work_without_any_bank_account(db, client, customer):  # R19.1, R11.6
    first = make_portfolio(db, customer, account_name="Growth", cash_balance=D("50.00"))
    second = make_portfolio(db, customer, account_name="Income")
    html = client.get("/transfer").get_data(as_text=True)
    assert "Link a bank account before transferring funds" in html                # deposits/withdrawals still need one
    assert "You can still move cash between your brokerage accounts." in html
    assert not re.search(r"<fieldset[^>]*disabled", html)                         # but the form is usable
    post_transfer(client, f"portfolio:{first}", f"portfolio:{second}", "50")
    assert (cash(db, first), cash(db, second)) == (D("0.00"), D("50.00"))


def test_one_portfolio_and_no_bank_keeps_the_form_disabled(db, client, customer):  # R11.6
    make_portfolio(db, customer)
    html = client.get("/transfer").get_data(as_text=True)
    assert re.search(r"<fieldset[^>]*disabled", html) and "You can still move cash" not in html


def test_moving_to_the_same_account_shows_an_error(db, client, funded):  # R19.2
    bank, portfolio = funded
    html = post_transfer(client, f"portfolio:{portfolio}", f"portfolio:{portfolio}", "10").get_data(as_text=True)
    assert 'data-error="to_account"' in html and "Choose a different account" in html
    assert cash(db, portfolio) == D("500.00")


def test_moving_more_than_the_balance_shows_insufficient_funds(db, client, customer, funded):  # R19.2
    bank, portfolio = funded
    other = make_portfolio(db, customer, cash_balance=D("0.00"))
    html = post_transfer(client, f"portfolio:{other}", f"portfolio:{portfolio}", "0.01").get_data(as_text=True)
    assert 'data-error="form">Insufficient funds<' in html
    assert (cash(db, other), cash(db, portfolio)) == (D("0.00"), D("500.00"))


# --- Support panel (D24, A20) ------------------------------------------------------------------------

def test_support_panel_shows_the_default_contact_details(client, funded):  # D24
    html = " ".join(client.get("/transfer").get_data(as_text=True).split())  # ignore line breaks in the template
    assert "Get Help Here" not in html
    assert ('Questions about a transfer? Contact support at <a href="mailto:support@example.com">'
            "support@example.com</a>, Monday–Friday, 9:00 AM–5:00 PM Arizona time.") in html


def test_support_panel_uses_the_configured_contact_details(db, db_env, customer):  # A20
    app = create_app({**db_env, "SUPPORT_EMAIL": "help@broker.example", "SUPPORT_HOURS": "every day, 8 AM–8 PM"})
    other_client = app.test_client()
    login(other_client, "casey@example.test", PASSWORD)
    html = other_client.get("/transfer").get_data(as_text=True)
    assert '<a href="mailto:help@broker.example">help@broker.example</a>, every day, 8 AM–8 PM.' in html


def test_missing_choices_show_field_errors(client, funded):
    html = post_transfer(client, "", "", "10").get_data(as_text=True)
    assert 'data-error="from_account"' in html and 'data-error="to_account"' in html


def test_the_form_remembers_what_was_typed_after_an_error(client, funded):
    bank, portfolio = funded
    html = post_transfer(client, f"bank:{bank}", f"portfolio:{portfolio}", "1.001").get_data(as_text=True)
    assert f'value="bank:{bank}" selected' in html and 'value="1.001"' in html


# --- Tampering and access (R2.5, N2.3) ----------------------------------------------------------------

def test_someone_elses_portfolio_in_the_form_gets_a_404(db, client, funded):  # R2.5
    bank, portfolio = funded
    stranger = make_user(db)
    theirs = make_portfolio(db, stranger, cash_balance=D("900.00"))
    response = post_transfer(client, f"bank:{bank}", f"portfolio:{theirs}", "10")
    assert response.status_code == 404
    response = post_transfer(client, f"portfolio:{theirs}", f"bank:{bank}", "10")
    assert response.status_code == 404
    response = post_transfer(client, f"portfolio:{portfolio}", f"portfolio:{theirs}", "10")  # R19.6
    assert response.status_code == 404
    response = post_transfer(client, f"portfolio:{theirs}", f"portfolio:{portfolio}", "10")
    assert response.status_code == 404
    assert cash(db, theirs) == D("900.00") and cash(db, portfolio) == D("500.00")


def test_transfers_need_a_csrf_token(db, client, funded):  # N2.3
    bank, portfolio = funded
    response = client.post("/transfer", data={"from_account": f"bank:{bank}", "to_account": f"portfolio:{portfolio}",
                                              "amount": "10", "transfer_date": "2026-09-28"})
    assert response.status_code == 400 and cash(db, portfolio) == D("500.00")


def test_page_needs_login(web_app):
    assert web_app.test_client().get("/transfer").headers["Location"].startswith("/signin")


# --- Add Bank Account (R10) ------------------------------------------------------------------------

def post_bank(client, **overrides):
    data = {"bank_name": "First Bank", "account_number": "000123456789", "routing_number": "021000021",
            "nickname": "Checking", "csrf_token": csrf_token(client, "/transfer")}
    data.update(overrides)
    return client.post("/transfer/bank-accounts", data=data)


def test_linking_a_bank_account(db, client, customer):  # R10.3, R10.4
    response = post_bank(client)
    assert response.status_code == 302
    html = client.get("/transfer").get_data(as_text=True)
    assert "Bank account &#34;Checking&#34; (•••• 6789) linked." in html or 'Bank account "Checking" (•••• 6789) linked.' in html
    assert 'data-name="Checking" data-detail="First Bank"' in html  # now in the From/To lists
    assert "First Bank •••• 6789" in html                     # and in the bank list


@pytest.mark.parametrize("field, value", [("routing_number", "12345678"), ("account_number", "12"),
                                          ("bank_name", ""), ("nickname", "x" * 101)])
def test_bank_account_errors_show_next_to_the_field(db, client, customer, field, value):  # R10.2
    html = post_bank(client, **{field: value}).get_data(as_text=True)
    assert f'data-error="{field}"' in html
    assert one(db, "SELECT COUNT(*) AS n FROM BankAccount")["n"] == 0
    assert "000123456789" not in html  # an entered account number is never echoed back


# --- From/To menus (D25) --------------------------------------------------------------------------------

def option_names(html):
    return re.findall(r'data-name="([^"]*)" data-detail="([^"]*)"', html)


def test_menu_options_show_the_name_and_balance_without_numbers(db, client, customer, funded):  # D25
    html = client.get("/transfer").get_data(as_text=True)
    pairs = option_names(html)
    # Each account appears once in From and once in To.
    assert pairs.count(("Checking", "First Bank")) == 2
    assert pairs.count(("Brokerage", "$500.00")) == 2
    assert "Brokerage · $500.00" in html      # the plain <select> text without JavaScript
    assert "cash" not in " ".join(detail for _, detail in pairs).lower()
    assert "data-account-select" in html


def test_same_names_get_the_last_4_digits_to_tell_them_apart(db, svc, client, customer, funded):  # D25
    svc(bank_accounts.add_bank_account, customer, "Second Bank", "000999994321", "021000021", "Checking")
    first = one(db, "SELECT portfolioID FROM Portfolio WHERE userID = %s", (customer,))["portfolioID"]
    make_portfolio(db, customer, account_name="Brokerage", cash_balance=D("1.00"))
    make_portfolio(db, customer, account_name="Savings", cash_balance=D("2.00"))
    html = client.get("/transfer").get_data(as_text=True)
    names = {name for name, _ in option_names(html)}
    assert {"Checking •••• 6789", "Checking •••• 4321", "Savings"} <= names
    assert "Checking" not in names and "Brokerage" not in names
    number = one(db, "SELECT account_number FROM Portfolio WHERE portfolioID = %s", (first,))["account_number"]
    assert f"Brokerage {mask_last4(number)}" in names


# --- Remove a bank account (R10.6-R10.8) ----------------------------------------------------------------

def remove_bank(client, bank_id, with_token=True):
    data = {"csrf_token": csrf_token(client, "/transfer")} if with_token else {}
    return client.post(f"/transfer/bank-accounts/{bank_id}/remove", data=data)


def test_bank_list_has_a_remove_button_for_each_account(client, funded):  # R10.6
    bank, _ = funded
    html = client.get("/transfer").get_data(as_text=True)
    assert "Your bank accounts" in html and "data-bank-list" in html
    assert f'action="/transfer/bank-accounts/{bank}/remove"' in html
    assert "data-confirm=" in html
    assert "000123456789" not in html


def test_removing_a_bank_account_through_the_page(db, client, funded):  # R10.6, R10.7
    bank, portfolio = funded
    assert post_transfer(client, f"bank:{bank}", f"portfolio:{portfolio}", "25").status_code == 302
    response = remove_bank(client, bank)
    assert response.status_code == 302 and response.headers["Location"] == "/transfer"
    html = client.get("/transfer").get_data(as_text=True)
    assert ("Bank account &#34;Checking&#34; (•••• 6789) removed. Past transfers still show it." in html
            or 'Bank account "Checking" (•••• 6789) removed. Past transfers still show it.' in html)
    assert f'value="bank:{bank}"' not in html and "data-bank-list" not in html
    assert "data-no-bank" in html   # the last one is gone: back to "Link a bank account" (R11.6)
    # Hidden, not deleted: the past deposit still points at it (R10.7).
    assert one(db, "SELECT is_active FROM BankAccount WHERE bankAccountID = %s", (bank,))["is_active"] == 0
    assert one(db, "SELECT COUNT(*) AS n FROM Transfer WHERE bankAccountID = %s", (bank,))["n"] == 1


def test_a_removed_bank_account_cannot_be_used(db, client, funded):  # R10.7
    bank, portfolio = funded
    remove_bank(client, bank)
    assert post_transfer(client, f"bank:{bank}", f"portfolio:{portfolio}", "10").status_code in (200, 404)
    assert cash(db, portfolio) == D("500.00")
    assert remove_bank(client, bank).status_code == 404   # already removed


def test_removing_someone_elses_bank_account_is_a_404(db, svc, client, funded):  # R10.8, R2.5
    other = make_user(db, email="other@example.test")
    theirs = svc(bank_accounts.add_bank_account, other, "Their Bank", "000555551111", "021000021", "Theirs")
    assert remove_bank(client, theirs.bank_account_id).status_code == 404
    assert one(db, "SELECT is_active FROM BankAccount WHERE bankAccountID = %s", (theirs.bank_account_id,))["is_active"] == 1


def test_removing_a_bank_account_needs_a_csrf_token(db, client, funded):  # N2.3
    bank, _ = funded
    assert remove_bank(client, bank, with_token=False).status_code == 400
    assert one(db, "SELECT is_active FROM BankAccount WHERE bankAccountID = %s", (bank,))["is_active"] == 1


# --- Filters -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, shown", [(D("0"), "$0.00"), (D("1234.5"), "$1,234.50"), (D("-12.345"), "-$12.35"),
                                          (D("99999999.99"), "$99,999,999.99"), (None, "—")])
def test_money_filter(value, shown):
    assert money(value) == shown
