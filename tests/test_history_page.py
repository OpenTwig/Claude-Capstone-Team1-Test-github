"""Transaction History (task 4.4; wf-8; R13, R17.1, R19.4, A12, P16)."""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.crypto import encrypt_field
from app.errors import NotFound, ValidationFailed
from app.services import history
from tests.factories import make_bank_account, make_portfolio, make_stock, make_user
from tests.web import login, make_login_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=AZ)
TODAY = date(2026, 9, 29)
PASSWORD = "correct horse battery"


def run(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        last = cur.lastrowid
    db.commit()
    return last


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def utc(at_local):
    return at_local.astimezone(timezone.utc).replace(tzinfo=None)


def trade(db, portfolio, issuer, action, quantity, price, at_local):
    total = (D(quantity) * D(price)).quantize(D("0.01"))
    tid = run(db, "INSERT INTO `Transaction` (portfolioID, issuerID, type, quantity, price_per_share, total_amount, status,"
                  " `timestamp`, order_type, timing) VALUES (%s, %s, %s, %s, %s, %s, 'Filled', %s, 'Market', 'Day Only')",
              (portfolio, issuer, action, D(quantity), D(price), total, utc(at_local)))
    run(db, "INSERT INTO History (portfolioID, transactionID, action, `timestamp`) VALUES (%s, %s, %s, %s)",
        (portfolio, tid, action, utc(at_local)))
    return tid


def transfer(db, kind, amount, at_local, *, to=None, source=None, bank=None):
    tid = run(db, "INSERT INTO Transfer (from_portfolioID, to_portfolioID, bankAccountID, amount, transfer_date, status,"
                  " created_at, transfer_type) VALUES (%s, %s, %s, %s, %s, 'Completed', %s, %s)",
              (source, to, bank, D(amount), at_local.date(), utc(at_local), kind))
    rows = {"Deposit": [(to, "Deposit")], "Withdrawal": [(source, "Withdrawal")],
            "Internal": [(source, "Transfer out"), (to, "Transfer in")]}[kind]
    for portfolio, action in rows:
        run(db, "INSERT INTO History (portfolioID, transferID, action, `timestamp`) VALUES (%s, %s, %s, %s)",
            (portfolio, tid, action, utc(at_local)))
    return tid


def number(db, portfolio):
    return one(db, "SELECT account_number FROM Portfolio WHERE portfolioID = %s", (portfolio,))["account_number"]


@pytest.fixture
def book(db, web_app, fixed_clock):
    """A customer with two portfolios, a bank account, and a mix of trades and transfers."""
    fixed_clock(NOW)
    with web_app.app_context():
        bank_number = encrypt_field("000123456789")
    user = make_login_user(db, PASSWORD, email="casey@example.test", name="Casey Customer")
    a = make_portfolio(db, user, account_name="Growth", cash_balance=D("1000.00"))
    b = make_portfolio(db, user, account_name="Income", cash_balance=D("100.00"))
    bank = make_bank_account(db, user, nickname="Checking", account_number=bank_number)
    aura = make_stock(db, symbol="AURA", price=D("42.18"))
    dune = make_stock(db, symbol="DUNE", price=D("63.05"))
    transfer(db, "Deposit", "500.00", datetime(2026, 9, 1, 9, 0, tzinfo=AZ), to=a, bank=bank)
    trade(db, a, aura, "Buy", "1.5", "42.18", datetime(2026, 9, 2, 10, 0, tzinfo=AZ))
    trade(db, a, dune, "Buy", "2", "60.00", datetime(2026, 9, 3, 10, 0, tzinfo=AZ))
    trade(db, a, aura, "Sell", "0.5", "44.00", datetime(2026, 9, 4, 10, 0, tzinfo=AZ))
    transfer(db, "Withdrawal", "25.00", datetime(2026, 9, 5, 10, 0, tzinfo=AZ), source=a, bank=bank)
    transfer(db, "Internal", "40.00", datetime(2026, 9, 6, 10, 0, tzinfo=AZ), source=a, to=b)
    trade(db, a, aura, "Buy", "1", "30.00", datetime(2026, 2, 1, 10, 0, tzinfo=AZ))      # 8 months ago
    return type("Book", (), {"user": user, "a": a, "b": b, "bank": bank, "aura": aura, "dune": dune})


# --- Date ranges (A12) ----------------------------------------------------------------------------------

@pytest.mark.parametrize("key, today, start", [
    ("30d", date(2026, 9, 29), date(2026, 8, 30)),
    ("3m", date(2026, 9, 29), date(2026, 6, 29)),
    ("6m", date(2026, 9, 29), date(2026, 3, 29)),
    ("12m", date(2026, 9, 29), date(2025, 9, 29)),
    ("ytd", date(2026, 9, 29), date(2026, 1, 1)),
    ("3m", date(2026, 5, 31), date(2026, 2, 28)),   # no 31 February: the month's last day
    ("6m", date(2026, 3, 15), date(2025, 9, 15)),   # across a year boundary
    ("bogus", date(2026, 9, 29), date(2026, 3, 29)),  # unknown -> Last 6 Months (the default)
    (None, date(2026, 9, 29), date(2026, 3, 29)),
])
def test_date_ranges(key, today, start):  # A12
    assert history.date_range(key, today) == (start, today)


def test_the_range_menu_matches_a12():
    assert list(history.RANGES.values()) == ["Last 30 Days", "Last 3 Months", "Last 6 Months", "Last 12 Months", "Year to Date"]
    assert history.DEFAULT_RANGE == "6m"


# --- The service (R13.2, R19.4, R10.7) --------------------------------------------------------------------

def test_rows_newest_first_with_descriptions(db, app_ctx, book):  # R13.2, R19.4
    rows = history.search_history(book.user, book.a, date(2026, 3, 29), TODAY)
    growth_b = number(db, book.b)[-4:]
    assert [(r.type, r.description, r.amount) for r in rows] == [
        ("Transfer out", f"Transfer to Income •••• {growth_b}", D("-40.00")),
        ("Withdrawal", "Withdrawal to Checking •••• 6789", D("-25.00")),
        ("Sell", "Sold 0.5 AURA @ $44.00", D("22.00")),
        ("Buy", "Bought 2 DUNE @ $60.00", D("-120.00")),
        ("Buy", "Bought 1.5 AURA @ $42.18", D("-63.27")),
        ("Deposit", "Deposit from Checking •••• 6789", D("500.00")),
    ]
    sell = rows[2]
    assert (sell.symbol, sell.quantity, sell.price, sell.status) == ("AURA", D("0.5"), D("44.00"), "Filled")
    deposit = rows[-1]
    assert (deposit.symbol, deposit.quantity, deposit.price, deposit.status) == (None, None, None, "Completed")
    assert deposit.at == datetime(2026, 9, 1, 9, 0, tzinfo=AZ)


def test_a_move_shows_on_both_accounts(db, app_ctx, book):  # R19.4
    [row] = history.search_history(book.user, book.b, date(2026, 3, 29), TODAY)
    assert (row.type, row.description, row.amount) == ("Transfer in", f"Transfer from Growth •••• {number(db, book.a)[-4:]}", D("40.00"))


def test_the_date_range_is_applied(db, app_ctx, book):  # R13.2
    assert len(history.search_history(book.user, book.a, date(2026, 3, 29), TODAY)) == 6
    assert len(history.search_history(book.user, book.a, date(2025, 9, 29), TODAY)) == 7  # includes February's buy
    assert [r.description for r in history.search_history(book.user, book.a, date(2026, 9, 3), date(2026, 9, 4))] \
        == ["Sold 0.5 AURA @ $44.00", "Bought 2 DUNE @ $60.00"]


def test_arizona_date_boundaries(db, app_ctx, book):  # R13.2: 23:30 Arizona is 06:30 UTC the next day
    late = datetime(2026, 9, 28, 23, 30, tzinfo=AZ)
    assert utc(late).date() == date(2026, 9, 29)
    trade(db, book.b, book.aura, "Buy", "1", "10.00", late)
    on_the_28th = history.search_history(book.user, book.b, date(2026, 9, 28), date(2026, 9, 28))
    assert [r.description for r in on_the_28th] == ["Bought 1 AURA @ $10.00"] and on_the_28th[0].at.date() == date(2026, 9, 28)
    assert history.search_history(book.user, book.b, date(2026, 9, 29), date(2026, 9, 29)) == []


def test_symbol_filter(db, app_ctx, book):  # R13.2
    rows = history.search_history(book.user, book.a, date(2026, 3, 29), TODAY, " aura ")
    assert [r.description for r in rows] == ["Sold 0.5 AURA @ $44.00", "Bought 1.5 AURA @ $42.18"]  # no transfers
    assert history.search_history(book.user, book.a, date(2026, 3, 29), TODAY, "ZZZ") == []
    with pytest.raises(ValidationFailed) as exc:
        history.search_history(book.user, book.a, date(2026, 3, 29), TODAY, "not a symbol!")
    assert exc.value.field == "symbol"


def test_removed_bank_accounts_still_show(db, app_ctx, book):  # R10.7
    run(db, "UPDATE BankAccount SET is_active = FALSE WHERE bankAccountID = %s", (book.bank,))
    rows = history.search_history(book.user, book.a, date(2026, 3, 29), TODAY)
    assert "Deposit from Checking •••• 6789" in [r.description for r in rows]


def test_customers_only_see_their_own_portfolios(db, app_ctx, book):  # R13.7, R2.5
    theirs = make_portfolio(db, make_user(db))
    with pytest.raises(NotFound):
        history.search_history(book.user, theirs, date(2026, 3, 29), TODAY)
    assert [c.portfolio_id for c in history.choices(book.user)] == [book.a, book.b]


def test_administrators_can_choose_every_portfolio(db, app_ctx, book):  # R13.6
    admin = make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    other = make_user(db, name="Alex Other")
    theirs = make_portfolio(db, other, account_name="Savings")
    labels = [c.label for c in history.choices(admin)]
    assert labels == [f"Alex Other: Savings •••• {number(db, theirs)[-4:]}",
                      f"Casey Customer: Growth •••• {number(db, book.a)[-4:]}",
                      f"Casey Customer: Income •••• {number(db, book.b)[-4:]}"]
    assert len(history.search_history(admin, book.a, date(2026, 3, 29), TODAY)) == 6
    with pytest.raises(NotFound):
        history.search_history(admin, 999999, date(2026, 3, 29), TODAY)


# --- P16: every result is in range and matches; none is missing (Hypothesis) --------------------------------

EVENTS = st.lists(
    st.tuples(st.integers(min_value=0, max_value=400),         # days before today
              st.integers(min_value=0, max_value=23),          # hour, Arizona
              st.sampled_from(["AAA", "BBB", "deposit"])),
    min_size=0, max_size=12,
)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None, max_examples=30)
@given(events=EVENTS, range_key=st.sampled_from(list(history.RANGES)), symbol=st.sampled_from([None, "AAA", "bbb"]))
def test_p16_results_match_the_filters_exactly(db, app_ctx, fixed_clock, events, range_key, symbol):  # P16
    fixed_clock(NOW)
    with db.cursor() as cur:
        for table in ("History", "`Transaction`", "Transfer"):
            cur.execute(f"DELETE FROM {table}")
    db.commit()
    user = one(db, "SELECT userID FROM `User` WHERE email = 'p16@example.test'")
    user = user["userID"] if user else make_user(db, email="p16@example.test")
    portfolio = one(db, "SELECT portfolioID FROM Portfolio WHERE userID = %s", (user,))
    portfolio = portfolio["portfolioID"] if portfolio else make_portfolio(db, user)
    stocks = {s: (one(db, "SELECT issuerID FROM Issuer WHERE ticker_symbol = %s", (s,)) or {}).get("issuerID")
              or make_stock(db, symbol=s) for s in ("AAA", "BBB")}
    bank = (one(db, "SELECT bankAccountID FROM BankAccount WHERE userID = %s", (user,)) or {}).get("bankAccountID") \
        or make_bank_account(db, user, account_number=encrypt_field("000123456789"))
    placed = []
    for days, hour, kind in events:
        at = datetime.combine(TODAY - timedelta(days=days), datetime.min.time(), AZ).replace(hour=hour)
        if kind == "deposit":
            transfer(db, "Deposit", "1.00", at, to=portfolio, bank=bank)
        else:
            trade(db, portfolio, stocks[kind], "Buy", "1", "1.00", at)
        placed.append((at, kind))

    start, end = history.date_range(range_key, TODAY)
    rows = history.search_history(user, portfolio, start, end, symbol)
    wanted = sorted((at for at, kind in placed
                     if start <= at.date() <= end and (symbol is None or kind == symbol.upper())), reverse=True)
    assert [r.at for r in rows] == wanted
    assert all(start <= r.at.date() <= end for r in rows)
    assert symbol is None or all(r.symbol == symbol.upper() for r in rows)


# --- The page (R13.1-R13.5) --------------------------------------------------------------------------------

@pytest.fixture
def signed_in(db, client, book):
    login(client, "casey@example.test", PASSWORD)
    return client


def test_history_page_defaults(db, signed_in, book):  # R13.1-R13.3
    html = signed_in.get("/history").get_data(as_text=True)
    assert "Transaction History" in html and re.search(rf'value="{book.a}" selected', html)
    assert '<option value="6m" selected>Last 6 Months</option>' in html
    for label in ("Last 30 Days", "Last 3 Months", "Last 12 Months", "Year to Date"):
        assert label in html
    assert "Transactions found from 03/29/2026 to 09/29/2026" in html
    for column in ("Date", "Transaction Type", "Symbol", "Description", "Quantity", "Price", "Amount", "Status"):
        assert f">{column}</th>" in html, column
    assert len(re.findall(r"data-history-row", html)) == 6
    assert "Bought 1.5 AURA @ $42.18" in html and "-$63.27" in html and "+$500.00" in html
    assert "000123456789" not in html


def test_history_filters_through_the_page(db, signed_in, book):  # R13.2
    html = signed_in.get(f"/history?portfolio={book.a}&range=12m&symbol=aura").get_data(as_text=True)
    assert len(re.findall(r"data-history-row", html)) == 3 and 'value="aura"' in html
    assert "Transactions found from 09/29/2025 to 09/29/2026" in html


def test_no_transactions_found(db, signed_in, book):  # R13.4
    html = signed_in.get(f"/history?portfolio={book.a}&symbol=ZZZ").get_data(as_text=True)
    assert "No transactions found" in html and "data-history-row" not in html
    assert "data-print-options" in html  # a longer range might still have something to print


def test_a_bad_symbol_shows_a_field_error(db, signed_in, book):
    html = signed_in.get(f"/history?portfolio={book.a}&symbol=a$b").get_data(as_text=True)
    assert 'data-error="symbol"' in html and "data-history-row" not in html


def test_print_layout(db, signed_in, book):  # R13.5
    html = signed_in.get("/history").get_data(as_text=True)
    assert re.search(r"<button[^>]*data-print-toggle", html) and '<script src="/static/js/history.js"></script>' in html
    # Print first asks how far back (user feedback): a form that keeps the portfolio and asks for the range.
    form = html[html.index("data-print-options"):html.index("</form>", html.index("data-print-options"))]
    assert "How far back do you want to print?" in form and 'name="print" value="1"' in form
    assert f'name="portfolio" value="{book.a}"' in form and '<option value="6m" selected>Last 6 Months</option>' in form
    assert re.search(r'<nav class="[^"]*d-print-none', html)                       # no nav bar on paper
    assert re.search(r'<form[^>]*class="[^"]*d-print-none[^"]*"[^>]*data-history-filters', html)  # no filters
    # The saved PDF's default name comes from this title (user feedback): account and dates, no bullets.
    assert 'data-print-title="Transaction History - Growth ' + number(db, book.a)[-4:] + ' - 03-29-2026 to 09-29-2026"' in html


def test_print_title_for_an_administrator(db, client, book):
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    login(client, "admin@example.test", PASSWORD)
    html = client.get(f"/history?portfolio={book.b}&range=30d").get_data(as_text=True)
    assert ('data-print-title="Transaction History - Casey Customer - Income ' + number(db, book.b)[-4:]
            + ' - 08-30-2026 to 09-29-2026"') in html


def test_another_customers_portfolio_is_a_404(db, signed_in, book):  # R13.7
    theirs = make_portfolio(db, make_user(db))
    assert signed_in.get(f"/history?portfolio={theirs}").status_code == 404
    assert signed_in.get("/history?portfolio=abc").status_code == 404


def test_customer_without_portfolios(db, client, fixed_clock):
    fixed_clock(NOW)
    make_login_user(db, PASSWORD, email="new@example.test")
    login(client, "new@example.test", PASSWORD)
    assert "data-no-portfolio" in client.get("/history").get_data(as_text=True)


def test_administrators_see_any_customers_history(db, client, book):  # R13.6, D19
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    login(client, "admin@example.test", PASSWORD)
    html = client.get(f"/history?portfolio={book.b}").get_data(as_text=True)
    assert "Casey Customer: Growth" in html and "Casey Customer: Income" in html
    assert len(re.findall(r"data-history-row", html)) == 1 and "Transfer from Growth" in html


def test_history_needs_login(web_app):
    assert web_app.test_client().get("/history").headers["Location"].startswith("/signin")


# --- Administrators: every customer at once, with whose row it is (user feedback) ------------------------

@pytest.fixture
def second_customer(db, book):
    """Another customer with one deposit, so "All customers" has two people in it."""
    other = make_user(db, name="Alex Other", email="alex@example.test")
    savings = make_portfolio(db, other, account_name="Savings")
    bank = make_bank_account(db, other, nickname="Main", account_number=book_bank_number(db, book))
    transfer(db, "Deposit", "75.00", datetime(2026, 9, 7, 9, 0, tzinfo=AZ), to=savings, bank=bank)
    return savings


def book_bank_number(db, book):
    return one(db, "SELECT account_number FROM BankAccount WHERE bankAccountID = %s", (book.bank,))["account_number"]


def test_all_customers_for_administrators(db, app_ctx, book, second_customer):  # R13.6
    admin = make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    rows = history.search_history(admin, history.ALL, date(2026, 3, 29), TODAY)
    assert len(rows) == 8   # Growth's 6, Income's move in, and Alex's deposit
    newest = rows[0]
    assert (newest.customer, newest.account, newest.description) == (
        "Alex Other", f"Savings •••• {number(db, second_customer)[-4:]}", "Deposit from Main •••• 6789")
    move_in = [r for r in rows if r.type == "Transfer in"][0]
    assert (move_in.customer, move_in.account) == ("Casey Customer", f"Income •••• {number(db, book.b)[-4:]}")
    assert [r.at for r in rows] == sorted((r.at for r in rows), reverse=True)


def test_customers_can_never_ask_for_everyone(db, app_ctx, book):  # R13.7
    with pytest.raises(NotFound):
        history.search_history(book.user, history.ALL, date(2026, 3, 29), TODAY)


def test_admin_page_defaults_to_all_customers(db, client, book, second_customer):  # R13.6
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    login(client, "admin@example.test", PASSWORD)
    html = client.get("/history").get_data(as_text=True)
    assert '<option value="all" selected>All customers</option>' in html
    assert ">Customer</th>" in html and ">Account</th>" in html
    assert len(re.findall(r"data-history-row", html)) == 8
    customers = re.findall(r"data-customer>([^<]+)<", html)
    assert customers[0] == "Alex Other" and customers.count("Casey Customer") == 7
    assert 'data-print-title="Transaction History - All customers - 03-29-2026 to 09-29-2026"' in html
    one_portfolio = client.get(f"/history?portfolio={book.b}").get_data(as_text=True)
    assert re.findall(r"data-customer>([^<]+)<", one_portfolio) == ["Casey Customer"]   # the columns stay for admins


def test_customers_never_see_the_customer_columns(db, signed_in, book):  # user feedback
    html = signed_in.get("/history").get_data(as_text=True)
    assert ">Customer</th>" not in html and "data-customer" not in html and "All customers" not in html
    assert signed_in.get("/history?portfolio=all").status_code == 404


# --- Pages, and printing a whole range (user feedback) ---------------------------------------------------

@pytest.fixture
def busy(db, book):
    """25 more buys in Growth on 25 September, one a minute, so its 6-month history has 31 rows."""
    for n in range(25):
        trade(db, book.a, book.aura, "Buy", "1", f"{10 + n}.00", datetime(2026, 9, 25, 10, n, tzinfo=AZ))
    return book


def test_history_page_service_pages_newest_first(db, app_ctx, busy):
    everything = history.search_history(busy.user, busy.a, date(2026, 3, 29), TODAY)
    first = history.history_page(busy.user, busy.a, date(2026, 3, 29), TODAY, page=1)
    second = history.history_page(busy.user, busy.a, date(2026, 3, 29), TODAY, page=2)
    assert (first.total, first.pages, len(first.rows), len(second.rows)) == (31, 2, 20, 11)
    assert list(first.rows) + list(second.rows) == everything        # the same rows, same order, no gaps
    assert history.history_page(busy.user, busy.a, date(2026, 3, 29), TODAY, page=9).page == 2
    symbol_only = history.history_page(busy.user, busy.a, date(2026, 3, 29), TODAY, "DUNE")
    assert symbol_only.total == 1 and symbol_only.rows[0].symbol == "DUNE"


def test_history_pages_on_the_page(db, signed_in, busy):
    html = signed_in.get(f"/history?portfolio={busy.a}&symbol=aura").get_data(as_text=True)
    assert len(re.findall(r"data-history-row", html)) == 20 and "Showing 1–20 of 27" in html
    assert f'href="/history?portfolio={busy.a}&amp;range=6m&amp;symbol=aura&amp;page=2#transactions"' in html
    page2 = signed_in.get(f"/history?portfolio={busy.a}&symbol=aura&page=2").get_data(as_text=True)
    assert len(re.findall(r"data-history-row", page2)) == 7 and "Showing 21–27 of 27" in page2


def test_printing_shows_the_whole_range_on_one_page(db, signed_in, busy):  # R13.5 (user feedback)
    html = signed_in.get(f"/history?portfolio={busy.a}&range=12m&print=1").get_data(as_text=True)
    assert len(re.findall(r"data-history-row", html)) == 32          # every row, including February's
    assert "data-autoprint" in html and 'data-pages="history"' not in html
    assert "All 32 transactions in this range, on one page for printing." in html
    assert 'data-print-title="Transaction History - Growth ' in html and "09-29-2025 to 09-29-2026" in html
    assert "data-autoprint" not in signed_in.get(f"/history?portfolio={busy.a}").get_data(as_text=True)


def test_customers_cannot_print_everyone(db, signed_in, book):  # R13.7
    assert signed_in.get("/history?portfolio=all&print=1").status_code == 404
