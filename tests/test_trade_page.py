"""Trade page, quote API, preview and confirm (task 4.1; wf-2; R8, R9, A3, A4, A13, D11, D16, D19, D20)."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.filters import shares
from tests.factories import make_portfolio, make_stock, make_user
from tests.web import csrf_token, login, make_login_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=AZ)      # Monday, market open (9:00-17:00)
CLOSED = datetime(2026, 9, 28, 17, 30, tzinfo=AZ)  # same day, after the close
PASSWORD = "correct horse battery"


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def cash(db, portfolio_id):
    return one(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (portfolio_id,))["cash_balance"]


def held(db, portfolio_id, issuer_id):
    row = one(db, "SELECT quantity FROM Inventory WHERE portfolioID = %s AND issuerID = %s", (portfolio_id, issuer_id))
    return row["quantity"] if row else None


def trades(db):
    return one(db, "SELECT COUNT(*) AS n FROM `Transaction`")["n"]


def set_price(db, issuer_id, price):
    one(db, "UPDATE Issuer SET current_price = %s WHERE issuerID = %s", (D(price), issuer_id))


@pytest.fixture
def market(db, fixed_clock):
    fixed_clock(NOW)
    aura = make_stock(db, symbol="AURA", company_name="Aurora Solar Works", price=D("40.00"))
    one(db, "INSERT INTO StockPrice (issuerID, price_date, open_price, close_price) VALUES (%s, %s, 38, 38)",
        (aura, date(2026, 9, 25)))
    return aura


@pytest.fixture
def trader(db, client, market):
    user = make_login_user(db, PASSWORD, email="casey@example.test")
    portfolio = make_portfolio(db, user, account_name="Brokerage", cash_balance=D("1000.00"))
    login(client, "casey@example.test", PASSWORD)
    client.user_id, client.portfolio = user, portfolio
    return client


def own(db, portfolio_id, issuer_id, quantity, average="30.00"):
    one(db, "INSERT INTO Inventory (portfolioID, issuerID, quantity, average_cost) VALUES (%s, %s, %s, %s)",
        (portfolio_id, issuer_id, D(quantity), D(average)))


def preview(client, **fields):
    data = {"portfolio": client.portfolio, "symbol": "AURA", "action": "Buy", "unit": "Dollars", "amount": "100",
            "order_type": "Market", "timing": "Day Only", "csrf_token": csrf_token(client, "/trade")}
    data.update(fields)
    return client.post("/trade/preview", data=data)


def nonce_of(html):
    return re.search(r'name="nonce" value="([^"]+)"', html).group(1)


def confirm(client, nonce, **extra):
    return client.post("/trade/confirm", data={"nonce": nonce, "csrf_token": csrf_token(client, "/trade"), **extra})


def place(client, **fields):
    html = preview(client, **fields).get_data(as_text=True)
    return confirm(client, nonce_of(html))


# --- The page (R8.1, R8.2, A13) -----------------------------------------------------------------------

def test_page_shows_the_portfolio_and_available_cash(db, trader):  # R8.1
    html = trader.get("/trade").get_data(as_text=True)
    number = one(db, "SELECT account_number FROM Portfolio WHERE portfolioID = %s", (trader.portfolio,))["account_number"]
    assert "Trade Ticket" in html and f"Brokerage •••• {number[-4:]}" in html and number not in html
    assert re.search(r"data-cash>\$1,000.00<", html)
    assert re.search(r"<form[^>]*data-ticket[^>]*hidden", html)  # the ticket waits for a symbol (wf-2)
    assert "Legal Information" in html and '<script src="/static/js/trade.js"></script>' in html


def test_second_portfolio_can_be_chosen(db, trader):  # R8.1
    other = make_portfolio(db, trader.user_id, account_name="Income", cash_balance=D("25.50"))
    html = trader.get(f"/trade?portfolio={other}").get_data(as_text=True)
    assert re.search(r"data-cash>\$25.50<", html) and re.search(rf'value="{other}" selected', html)


def test_without_a_portfolio_the_page_says_so(db, client, market):  # R8.1
    make_login_user(db, PASSWORD, email="new@example.test")
    login(client, "new@example.test", PASSWORD)
    html = client.get("/trade").get_data(as_text=True)
    assert "data-no-portfolio" in html and "data-ticket" not in html


def test_looking_up_a_symbol_shows_the_quote_and_the_ticket(db, trader):  # R8.2, R8.3, D17
    html = trader.get(f"/trade?portfolio={trader.portfolio}&symbol=aura").get_data(as_text=True)
    assert "Aurora Solar Works (AURA)" in html and re.search(r"data-quote-price>\$40.00<", html)
    assert "Market open · closes 5:00 PM" in html and "text-bg-success" in html
    assert re.search(r"data-quote-change>\+\$2.00 \(\+5.26%\)<", html)
    assert not re.search(r"<form[^>]*data-ticket[^>]*hidden", html)
    for field in ("Action", "Unit", "Amount", "Order Type", "Timing", "Clear", "Submit"):
        assert field in html, field
    assert "<option>Market</option>" in html and "<option>Day Only</option>" in html  # A4
    assert "Bid" not in html and "Ask" not in html


def test_the_quote_shows_when_the_market_is_closed(db, trader, fixed_clock):  # R8.2
    fixed_clock(CLOSED)
    html = trader.get("/trade?symbol=AURA").get_data(as_text=True)
    assert "Market closed · opens Tue 9:00 AM" in html and "text-bg-secondary" in html


@pytest.mark.parametrize("symbol", ["NOPE", "GONE"])
def test_unknown_or_delisted_symbol_is_not_found(db, trader, symbol):  # R8.2, D20
    make_stock(db, symbol="GONE", is_listed=False)
    html = trader.get(f"/trade?symbol={symbol}").get_data(as_text=True)
    assert re.search(r'data-error="symbol">Symbol not found<', html) and "data-quote-name></h2>" in html


def test_a_held_delisted_stock_still_gets_a_quote(db, trader):  # R9.10, D20
    gone = make_stock(db, symbol="GONE", company_name="Gone Inc", price=D("5.00"), is_listed=False)
    own(db, trader.portfolio, gone, "3")
    html = trader.get("/trade?symbol=GONE").get_data(as_text=True)
    assert "Gone Inc (GONE)" in html and re.search(r"data-quote-owned>3<", html)


def test_search_history_keeps_the_last_5_symbols(db, trader):  # A13
    for symbol in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"):
        make_stock(db, symbol=symbol)
    for symbol in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "BBB", "NOPE"):
        trader.get(f"/trade?symbol={symbol}")
    html = trader.get("/trade").get_data(as_text=True)
    assert re.findall(r"data-history-symbol>(\w+)<", html) == ["BBB", "FFF", "EEE", "DDD", "CCC"]


def test_quote_shows_shares_owned_and_their_market_value(db, trader, market):  # R9.1 (user feedback)
    own(db, trader.portfolio, market, "1.5")
    html = trader.get("/trade?symbol=AURA").get_data(as_text=True)
    assert re.search(r"data-quote-owned>1.5<", html) and re.search(r"data-quote-value>\$60.00<", html)  # 1.5 x $40
    assert re.search(r"data-quote-change>\+\$2.00 \(\+5.26%\)<", html)  # change since close stays (R8.2)
    assert re.search(r'<option value="Sell" data-sell>Sell</option>', html)


def test_sell_is_greyed_out_without_shares(db, trader):  # R9.1 (user feedback); the server still checks (R9.4)
    html = trader.get("/trade?symbol=AURA").get_data(as_text=True)
    assert re.search(r"data-quote-owned>0<", html) and re.search(r"data-quote-value>\$0.00<", html)
    assert re.search(r'<option value="Sell" data-sell disabled>Sell</option>', html)
    posted = preview(trader, action="Sell", unit="Shares", amount="1").get_data(as_text=True)
    assert re.search(r'data-error="form">Quantity exceeds shares owned<', posted) and trades(db) == 0


def test_market_value_rounds_to_the_cent(db, trader, market):
    set_price(db, market, "33.33")
    own(db, trader.portfolio, market, "0.000237")  # 0.00789921 -> $0.01
    assert re.search(r"data-quote-value>\$0.01<", trader.get("/trade?symbol=AURA").get_data(as_text=True))


# --- Quote API (R8.2, R9.1, R2.5) ---------------------------------------------------------------------

def test_quote_api_returns_the_quote(db, trader):  # R8.2, R9.1
    own(db, trader.portfolio, one(db, "SELECT issuerID FROM Issuer WHERE ticker_symbol = 'AURA'")["issuerID"], "1.5")
    body = trader.get(f"/api/quote/aura?portfolio={trader.portfolio}").get_json()
    assert body["symbol"] == "AURA" and body["company_name"] == "Aurora Solar Works"
    assert (body["price"], body["previous_close"], body["change"], body["change_pct"]) == ("40.00", "38.00", "2.00", "5.26")
    assert body["price_text"] == "$40.00" and body["change_text"] == "+$2.00 (+5.26%)"
    assert body["market_open"] is True and body["market_text"] == "Market open · closes 5:00 PM"
    assert D(body["shares_owned"]) == D("1.5") and body["history"] == ["AURA"]
    assert body["shares_owned_text"] == "1.5" and body["market_value_text"] == "$60.00"


def test_quote_api_404s(db, trader):  # R8.2, R2.5, D20
    make_stock(db, symbol="GONE", is_listed=False)
    theirs = make_portfolio(db, make_user(db))
    assert trader.get("/api/quote/NOPE").status_code == 404
    assert trader.get("/api/quote/NOPE").get_json() == {"error": "Symbol not found"}
    assert trader.get("/api/quote/GONE").status_code == 404
    assert trader.get(f"/api/quote/AURA?portfolio={theirs}").status_code == 404
    assert trader.get("/api/quote/AURA?portfolio=abc").status_code == 404


# --- Buying (R8) --------------------------------------------------------------------------------------

def test_preview_shows_the_order_and_changes_nothing(db, trader, market):  # R8.3, R8.6, D11
    html = preview(trader, amount="100").get_data(as_text=True)
    assert "Review Your Order" in html and "Aurora Solar Works (AURA)" in html
    assert re.search(r"data-review-quantity>2.5 shares<", html)          # $100 / $40 (A3)
    assert re.search(r"data-review-price>\$40.00<", html) and re.search(r"data-review-total>\$100.00<", html)
    assert "Estimated price" in html and "Estimated total" in html and "estimates" in html
    assert "never cost more than $100.00" in html
    assert cash(db, trader.portfolio) == D("1000.00") and trades(db) == 0


def test_confirming_a_buy_executes_it(db, trader, market):  # R8.7, R8.8
    response = place(trader, amount="100")
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and "Order Filled" in html
    assert "Bought 2.5 AURA at $40.00 for $100.00." in html and re.search(r"data-cash-after>\$900.00<", html)
    assert cash(db, trader.portfolio) == D("900.00") and held(db, trader.portfolio, market) == D("2.5")
    t = one(db, "SELECT type, quantity, price_per_share, total_amount, status, order_type, timing FROM `Transaction`")
    assert (t["type"], t["quantity"], t["price_per_share"], t["total_amount"], t["status"], t["order_type"], t["timing"]) \
        == ("Buy", D("2.5"), D("40.00"), D("100.00"), "Filled", "Market", "Day Only")


def test_buying_by_shares(db, trader, market):  # A3
    html = place(trader, unit="Shares", amount="0.5").get_data(as_text=True)
    assert "Bought 0.5 AURA at $40.00 for $20.00." in html and cash(db, trader.portfolio) == D("980.00")


def test_dollar_orders_never_spend_more_than_the_amount(db, trader, market):  # A3, D11
    set_price(db, market, "33.33")
    html = place(trader, amount="100").get_data(as_text=True)
    assert "Bought 3.0003 AURA at $33.33 for $100.00." in html               # floor(100 / 33.33, 6 places)
    assert cash(db, trader.portfolio) == D("1000.00") - D("100.00")        # 3.0003 x 33.33 = 99.999999 -> $100.00


def test_ticket_amounts_can_have_thousands_separators(db, trader, market):  # D27
    one(db, "UPDATE Portfolio SET cash_balance = 5000 WHERE portfolioID = %s", (trader.portfolio,))
    html = place(trader, amount="1,000").get_data(as_text=True)
    assert "Bought 25 AURA at $40.00 for $1,000.00." in html


def test_market_closed_is_rejected(db, trader, fixed_clock):  # R8.4
    fixed_clock(CLOSED)
    html = preview(trader).get_data(as_text=True)
    assert re.search(r'data-error="form">Market is closed<', html) and trades(db) == 0


def test_insufficient_funds(db, trader):  # R8.5
    html = preview(trader, amount="1000.01").get_data(as_text=True)
    assert re.search(r'data-error="form">Insufficient funds<', html) and trades(db) == 0


def test_not_enough_shares_available(db, trader, market):  # R8.11, D16
    one(db, "UPDATE Issuer SET shares_available = 2 WHERE issuerID = %s", (market,))
    html = preview(trader, unit="Shares", amount="3").get_data(as_text=True)
    assert re.search(r'data-error="form">Not enough shares available<', html)


@pytest.mark.parametrize("fields, field, message", [
    ({"amount": "abc"}, "amount", "Enter an amount like 250"),
    ({"amount": "0"}, "amount", "greater than $0.00"),
    ({"amount": "1.001"}, "amount", "at most 2 decimal places"),
    ({"unit": "Shares", "amount": "1.0000001"}, "amount", "at most 6 decimal places"),
    ({"amount": "100000000"}, "amount", "The largest amount you can enter is $99,999,999.99."),
    ({"action": ""}, "action", "Choose Buy or Sell."),
    ({"unit": "Euros"}, "unit", "Choose Dollars or Shares."),
    ({"order_type": "Limit"}, "order_type", "Only Market orders are available."),
])
def test_ticket_errors_show_next_to_the_field(db, trader, fields, field, message):  # R8.3, A3, A4
    html = preview(trader, **fields).get_data(as_text=True)
    assert f'data-error="{field}"' in html and message in html
    assert trades(db) == 0 and "Review Your Order" not in html


def test_the_ticket_keeps_what_was_typed_after_an_error(db, trader):
    html = preview(trader, amount="1000.01", unit="Dollars", action="Buy").get_data(as_text=True)
    assert 'value="1000.01"' in html and "<option selected>Buy</option>" in html


# --- Selling (R9) -------------------------------------------------------------------------------------

def test_selling_shares(db, trader, market):  # R9.1, R9.6, R9.7, CR08
    own(db, trader.portfolio, market, "5")
    supply = one(db, "SELECT shares_available FROM Issuer WHERE issuerID = %s", (market,))["shares_available"]
    assert re.search(r"data-quote-owned>5<", trader.get("/trade?symbol=AURA").get_data(as_text=True))
    html = place(trader, action="Sell", unit="Shares", amount="2").get_data(as_text=True)
    assert "Sold 2 AURA at $40.00 for $80.00." in html and "Total proceeds" in html
    assert cash(db, trader.portfolio) == D("1080.00") and held(db, trader.portfolio, market) == D("3")
    assert one(db, "SELECT shares_available FROM Issuer WHERE issuerID = %s", (market,))["shares_available"] == supply + 2


def test_selling_more_than_owned(db, trader, market):  # R9.4
    own(db, trader.portfolio, market, "1")
    html = preview(trader, action="Sell", unit="Shares", amount="1.5").get_data(as_text=True)
    assert re.search(r'data-error="form">Quantity exceeds shares owned<', html)


def test_a_held_delisted_stock_can_be_sold_but_not_bought(db, trader):  # R9.10, D20
    gone = make_stock(db, symbol="GONE", price=D("5.00"), is_listed=False)
    own(db, trader.portfolio, gone, "3")
    assert "Symbol not found" in preview(trader, symbol="GONE").get_data(as_text=True)
    html = place(trader, symbol="GONE", action="Sell", unit="Shares", amount="3").get_data(as_text=True)
    assert "Sold 3 GONE at $5.00 for $15.00." in html and held(db, trader.portfolio, gone) is None


# --- Confirmation rules (R8.6, R8.7, R8.9, D11, design: Trading) ---------------------------------------

def test_checks_run_again_at_confirm(db, trader, fixed_clock):  # R8.7: the market closed in between
    fixed_clock(datetime(2026, 9, 28, 16, 59, 30, tzinfo=AZ))
    nonce = nonce_of(preview(trader).get_data(as_text=True))
    fixed_clock(datetime(2026, 9, 28, 17, 0, 10, tzinfo=AZ))  # closed, but the review hasn't expired
    html = confirm(trader, nonce).get_data(as_text=True)
    assert re.search(r'data-error="form">Market is closed<', html)
    assert trades(db) == 0 and cash(db, trader.portfolio) == D("1000.00")


def test_it_executes_at_the_price_when_confirmed(db, trader, market):  # D11
    nonce = nonce_of(preview(trader, unit="Shares", amount="2").get_data(as_text=True))
    set_price(db, market, "41.25")
    html = confirm(trader, nonce).get_data(as_text=True)
    assert "Bought 2 AURA at $41.25 for $82.50." in html and cash(db, trader.portfolio) == D("917.50")


def test_an_expired_review_is_refused(db, trader, fixed_clock):  # design: 2-minute expiry
    nonce = nonce_of(preview(trader).get_data(as_text=True))
    fixed_clock(NOW + timedelta(minutes=2, seconds=1))
    html = confirm(trader, nonce).get_data(as_text=True)
    assert "This order has expired. Please review it again." in html and trades(db) == 0


def test_a_review_just_inside_2_minutes_is_accepted(db, trader, fixed_clock):
    nonce = nonce_of(preview(trader).get_data(as_text=True))
    fixed_clock(NOW + timedelta(minutes=2))
    assert "Order Filled" in confirm(trader, nonce).get_data(as_text=True)


def test_a_review_can_only_be_used_once(db, trader):  # R8.9, double submission
    nonce = nonce_of(preview(trader).get_data(as_text=True))
    assert "Order Filled" in confirm(trader, nonce).get_data(as_text=True)
    again = confirm(trader, nonce).get_data(as_text=True)
    assert "This order has expired. Please review it again." in again
    assert trades(db) == 1 and cash(db, trader.portfolio) == D("900.00")


def test_a_wrong_nonce_is_refused(db, trader):
    preview(trader)
    html = confirm(trader, "made-up").get_data(as_text=True)
    assert "This order has expired" in html and trades(db) == 0
    assert "This order has expired" in confirm(trader, "").get_data(as_text=True)  # the pending order was used up


def test_tampered_confirm_fields_are_ignored(db, trader, market):  # tampering: the server recomputes
    theirs = make_portfolio(db, make_user(db), cash_balance=D("5000.00"))
    nonce = nonce_of(preview(trader, unit="Shares", amount="2").get_data(as_text=True))
    html = confirm(trader, nonce, price="0.01", total="0.02", quantity="1000", amount="1000", symbol="ZZZ",
                   action="Sell", portfolio=str(theirs)).get_data(as_text=True)
    assert "Bought 2 AURA at $40.00 for $80.00." in html
    assert cash(db, trader.portfolio) == D("920.00") and cash(db, theirs) == D("5000.00")


def test_tampered_preview_fields_are_ignored(db, trader):  # the price and total are never read from the form
    html = preview(trader, unit="Shares", amount="2", price="0.01", total="0.02", quantity="99").get_data(as_text=True)
    assert re.search(r"data-review-total>\$80.00<", html) and re.search(r"data-review-quantity>2 shares<", html)


def test_someone_elses_portfolio_is_a_404(db, trader):  # R2.5
    theirs = make_portfolio(db, make_user(db), cash_balance=D("5000.00"))
    assert preview(trader, portfolio=str(theirs)).status_code == 404
    assert preview(trader, portfolio="abc").status_code == 404
    assert trader.get(f"/trade?portfolio={theirs}").status_code == 404
    assert trades(db) == 0 and cash(db, theirs) == D("5000.00")


def test_preview_and_confirm_need_a_csrf_token(db, trader):  # N2.3
    assert trader.post("/trade/preview", data={"portfolio": trader.portfolio, "symbol": "AURA", "action": "Buy",
                                               "unit": "Dollars", "amount": "10"}).status_code == 400
    nonce = nonce_of(preview(trader).get_data(as_text=True))
    assert trader.post("/trade/confirm", data={"nonce": nonce}).status_code == 400
    assert trades(db) == 0


# --- Access (D19, R2) ---------------------------------------------------------------------------------

def test_administrators_get_403(db, client, market):  # D19
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    login(client, "admin@example.test", PASSWORD)
    token = csrf_token(client, "/stocks")
    assert client.get("/trade").status_code == 403
    assert client.get("/api/quote/AURA").status_code == 403
    assert client.post("/trade/preview", data={"csrf_token": token}).status_code == 403
    assert client.post("/trade/confirm", data={"csrf_token": token}).status_code == 403


def test_trade_needs_login(web_app):
    client = web_app.test_client()
    for path in ("/trade", "/api/quote/AURA"):
        assert client.get(path).headers["Location"].startswith("/signin")


def test_stock_detail_links_customers_to_the_trade_page(db, trader):
    html = trader.get("/stocks/AURA").get_data(as_text=True)
    assert 'href="/trade?symbol=AURA"' in html


# --- Filter --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, shown", [(D("2.500000"), "2.5"), (D("1234"), "1,234"), (D("0.000237"), "0.000237"),
                                          (D("0"), "0"), (None, "—")])
def test_shares_filter(value, shown):
    assert shares(value) == shown
