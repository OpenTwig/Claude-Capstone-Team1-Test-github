"""Stocks pages and market data (task 4.3; wf-1; R7, A8, A14, A17, A19, D5, D17, D18, D20)."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.errors import SymbolNotFound
from app.services import valuation
from tests.factories import make_portfolio, make_stock
from tests.web import login, make_login_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=AZ)  # a Monday, market open
TODAY = date(2026, 9, 28)
YESTERDAY = date(2026, 9, 25)  # the Friday before
PASSWORD = "correct horse battery"


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def price_row(db, issuer_id, day, open_price, close_price):
    one(db, "INSERT INTO StockPrice (issuerID, price_date, open_price, close_price) VALUES (%s, %s, %s, %s)",
        (issuer_id, day, D(open_price), D(close_price)))


def stock(db, symbol, previous, current, **kwargs):
    """A stock that closed at `previous` last trading day and now trades at `current`."""
    issuer_id = make_stock(db, symbol=symbol, price=D(current), company_name=f"{symbol} Corp", **kwargs)
    price_row(db, issuer_id, YESTERDAY, previous, previous)
    price_row(db, issuer_id, TODAY, previous, current)
    return issuer_id


def trade(db, portfolio_id, issuer_id, at_local, action="Buy"):
    at = at_local.astimezone(timezone.utc).replace(tzinfo=None)
    one(db, "INSERT INTO `Transaction` (portfolioID, issuerID, type, quantity, price_per_share, total_amount, status,"
            " `timestamp`, order_type, timing) VALUES (%s, %s, %s, 1, 10.00, 10.00, 'Filled', %s, 'Market', 'Day Only')",
        (portfolio_id, issuer_id, action, at))


@pytest.fixture
def clocked(fixed_clock):
    fixed_clock(NOW)


@pytest.fixture
def signed_in(db, client, clocked):
    user = make_login_user(db, PASSWORD, email="casey@example.test")
    login(client, "casey@example.test", PASSWORD)
    client.user_id = user
    return client


# --- Top Gainers (A14) -------------------------------------------------------------------------------

def test_top_gainers_are_the_5_highest_percent_changes(db, app_ctx, clocked):  # R7.3, A14
    stock(db, "AAA", "100.00", "110.00")   # +10%
    stock(db, "BBB", "10.00", "12.00")     # +20%
    stock(db, "CCC", "50.00", "45.00")     # -10%
    stock(db, "DDD", "200.00", "201.00")   # +0.5%
    stock(db, "EEE", "20.00", "22.00")     # +10%, ties with AAA: symbol order
    stock(db, "FFF", "1.00", "1.30")       # +30%
    stock(db, "GGG", "80.00", "60.00")     # -25%
    movers = valuation.top_gainers()
    assert [m.symbol for m in movers] == ["FFF", "BBB", "AAA", "EEE", "DDD"]
    fff = movers[0]
    assert (fff.price, fff.previous_close, fff.change, fff.change_pct) == (D("1.30"), D("1.00"), D("0.30"), D("30.00"))


def test_gainers_rank_by_the_exact_ratio_not_the_rounded_one(db, app_ctx, clocked):  # A14
    stock(db, "AAA", "300.00", "300.02")    # +0.00667%, shows 0.01%
    stock(db, "ZZZ", "100.00", "100.01")    # +0.01%, shows 0.01%: a rounded tie would put AAA first
    assert [m.symbol for m in valuation.top_gainers()] == ["ZZZ", "AAA"]


def test_delisted_stocks_are_left_out_of_gainers(db, app_ctx, clocked):  # R7.9, D20
    stock(db, "GONE", "10.00", "20.00", is_listed=False)
    stock(db, "KEPT", "10.00", "10.50")
    assert [m.symbol for m in valuation.top_gainers()] == ["KEPT"]


def test_a_stock_with_no_earlier_close_shows_no_change(db, app_ctx, clocked):  # A8
    make_stock(db, symbol="NEW", price=D("25.00"))
    [m] = valuation.top_gainers()
    assert (m.previous_close, m.change, m.change_pct) == (D("25.00"), D("0.00"), D("0.00"))


def test_previous_close_is_the_last_trading_day_before_today(db, app_ctx, clocked):  # A8
    issuer = make_stock(db, symbol="OLD", price=D("12.00"))
    price_row(db, issuer, date(2026, 9, 24), "9.00", "9.00")
    price_row(db, issuer, YESTERDAY, "9.00", "10.00")
    [m] = valuation.top_gainers()
    assert m.previous_close == D("10.00") and m.change == D("2.00") and m.change_pct == D("20.00")


# --- Trending (D18, A19) -----------------------------------------------------------------------------

def test_trending_counts_todays_trades_in_arizona_time(db, app_ctx, clocked):  # R7.3, D18, A19
    customer_portfolio = make_portfolio(db, make_login_user(db, PASSWORD))
    ids = {s: stock(db, s, "10.00", "10.00") for s in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "ZZZ")}
    for symbol, count in {"BBB": 3, "AAA": 2, "CCC": 2, "DDD": 1, "EEE": 1, "FFF": 1}.items():
        for n in range(count):
            trade(db, customer_portfolio, ids[symbol], datetime(2026, 9, 28, 9, n, tzinfo=AZ),
                  "Sell" if n % 2 else "Buy")  # buys and sells both count
    # Yesterday doesn't count, even at 23:30 Arizona time, which is 06:30 UTC today.
    for n in range(5):
        trade(db, customer_portfolio, ids["ZZZ"], datetime(2026, 9, 27, 23, 30, tzinfo=AZ))
    # 00:30 Arizona today is 07:30 UTC: it counts.
    trade(db, customer_portfolio, ids["FFF"], datetime(2026, 9, 28, 0, 30, tzinfo=AZ))
    movers = valuation.trending()
    assert [(m.symbol, m.trades_today) for m in movers] == [("BBB", 3), ("AAA", 2), ("CCC", 2), ("FFF", 2), ("DDD", 1)]


def test_trending_leaves_out_untraded_and_delisted_stocks(db, app_ctx, clocked):  # A19, D20
    portfolio = make_portfolio(db, make_login_user(db, PASSWORD))
    gone = stock(db, "GONE", "10.00", "10.00", is_listed=False)
    kept = stock(db, "KEPT", "10.00", "10.00")
    stock(db, "IDLE", "10.00", "10.00")
    trade(db, portfolio, gone, NOW)
    trade(db, portfolio, kept, NOW)
    assert [m.symbol for m in valuation.trending()] == ["KEPT"]


def test_no_trades_today_means_an_empty_trending_panel(db, app_ctx, clocked):  # A19
    stock(db, "AAA", "10.00", "11.00")
    assert valuation.trending() == []


# --- Search (R7.1, R7.2, R7.5) ---------------------------------------------------------------------

def test_search_results_carry_prices_and_changes(db, app_ctx, clocked):  # R7.1, R7.5
    stock(db, "AURA", "40.00", "42.00")
    stock(db, "BLDR", "80.00", "80.00")
    stock(db, "AUX", "10.00", "9.00", is_listed=False)
    [m] = valuation.search("au")
    assert (m.symbol, m.price, m.change, m.change_pct) == ("AURA", D("42.00"), D("2.00"), D("5.00"))


# --- Detail (R7.4) -----------------------------------------------------------------------------------

def test_detail_has_the_numbers_the_page_needs(db, app_ctx, clocked):  # R7.4, A8, D17
    issuer = make_stock(db, symbol="DUNE", company_name="Dune Logistics", price=D("63.05"),
                        description="Moves things.", exchange="NYSE")
    price_row(db, issuer, date(2025, 9, 20), "40.00", "41.00")   # more than a year ago: not charted
    price_row(db, issuer, YESTERDAY, "60.00", "61.50")
    price_row(db, issuer, TODAY, "61.50", "63.05")
    detail = valuation.stock_detail("dune")
    assert detail.mover.symbol == "DUNE" and detail.mover.previous_close == D("61.50")
    assert detail.mover.change == D("1.55") and detail.mover.change_pct == D("2.52")
    assert detail.open_price == D("61.50")
    assert (detail.exchange, detail.description) == ("NYSE", "Moves things.")
    assert detail.history == ((YESTERDAY, D("61.50")), (TODAY, D("63.05")))


def test_detail_before_the_first_tick_has_no_open(db, app_ctx, clocked):  # A8
    issuer = make_stock(db, symbol="QUIET", price=D("5.00"))
    price_row(db, issuer, YESTERDAY, "5.00", "5.00")
    assert valuation.stock_detail("QUIET").open_price is None


@pytest.mark.parametrize("symbol", ["NOPE", "GONE", "", "not a symbol!"])
def test_detail_for_unknown_or_delisted_is_symbol_not_found(db, app_ctx, clocked, symbol):  # R7.9, D20
    make_stock(db, symbol="GONE", is_listed=False)
    with pytest.raises(SymbolNotFound):
        valuation.stock_detail(symbol)


# --- Pages -------------------------------------------------------------------------------------------

def chart_json(html):
    return json.loads(re.search(r'<script type="application/json" id="chart-data">(.*?)</script>', html, re.S).group(1))


def test_search_page_shows_gainers_and_trending_only(db, signed_in):  # R7.3, R7.6, R7.7, D5, D17
    stock(db, "AURA", "40.00", "42.00")
    html = signed_in.get("/stocks").get_data(as_text=True)
    assert "Stock Search" in html and 'name="q"' in html
    assert 'data-panel="gainers"' in html and 'data-panel="trending"' in html
    for missing in ("Most active", "Most Active", "Top losers", "Top Losers", "Bid", "Ask"):
        assert missing not in html, missing
    assert "AURA" in html and "$42.00" in html and "+$2.00 (+5.00%)" in html   # R7.5
    assert "No trades yet today." in html
    assert chart_json(html)["sparks"]["AURA"] == ["40.00", "42.00"]
    assert '<script src="/static/js/stocks.js"></script>' in html


def test_searching_lists_matches(db, signed_in):  # R7.1
    stock(db, "AURA", "40.00", "42.00")
    stock(db, "BLDR", "80.00", "79.00")
    html = signed_in.get("/stocks?q=bld").get_data(as_text=True)
    results = html[html.index("data-results"):html.index('data-panel="gainers"')]
    assert "BLDR" in results and "AURA" not in results and "-$1.00 (-1.25%)" in results
    assert 'href="/stocks/BLDR"' in results


def test_no_matches_shows_the_message(db, signed_in):  # R7.2
    html = signed_in.get("/stocks?q=zzz").get_data(as_text=True)
    assert "No stocks found matching &#34;zzz&#34;" in html


def test_the_query_is_escaped(db, signed_in):  # N2 (XSS)
    html = signed_in.get("/stocks?q=<script>alert(1)</script>").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_a_too_long_search_shows_a_field_error(db, signed_in):  # R7.1
    html = signed_in.get("/stocks?q=" + "a" * 101).get_data(as_text=True)
    assert 'data-error="q"' in html and "100 characters or fewer" in html


def test_detail_page_matches_the_stock_price_rows(db, signed_in):  # R7.4, D17
    issuer = make_stock(db, symbol="DUNE", company_name="Dune Logistics", price=D("63.05"),
                        description="Moves things.", exchange="NYSE")
    price_row(db, issuer, YESTERDAY, "60.00", "61.50")
    price_row(db, issuer, TODAY, "61.50", "63.05")
    html = signed_in.get("/stocks/dune").get_data(as_text=True)
    assert "Dune Logistics (DUNE)" in html and "$63.05" in html and "+$1.55 (+2.52%)" in html
    assert re.search(r"data-previous-close>\$61.50<", html) and re.search(r"data-open>\$61.50<", html)
    assert "Moves things." in html and re.search(r"data-exchange>NYSE<", html)
    assert "Bid" not in html and "Ask" not in html
    rows = []
    with db.cursor() as cur:
        cur.execute("SELECT price_date, close_price FROM StockPrice WHERE issuerID = %s ORDER BY price_date", (issuer,))
        rows = cur.fetchall()
    db.commit()
    assert chart_json(html)["history"] == {"labels": [r["price_date"].isoformat() for r in rows],
                                           "values": [str(r["close_price"]) for r in rows]}


def test_detail_leaves_out_an_empty_exchange(db, signed_in):  # R7.4
    make_stock(db, symbol="PLAIN", price=D("5.00"))
    html = signed_in.get("/stocks/PLAIN").get_data(as_text=True)
    assert "data-exchange" not in html and "No description yet." in html


@pytest.mark.parametrize("symbol", ["NOPE", "GONE"])
def test_unknown_or_delisted_detail_is_a_404(db, signed_in, symbol):  # R7.9, D20
    make_stock(db, symbol="GONE", is_listed=False)
    response = signed_in.get(f"/stocks/{symbol}")
    assert response.status_code == 404 and "Symbol not found" in response.get_data(as_text=True)


def test_delisted_stocks_never_show_on_the_page(db, signed_in):  # R7.9, D20
    stock(db, "GONE", "10.00", "20.00", is_listed=False)
    html = signed_in.get("/stocks?q=gone").get_data(as_text=True)
    assert 'data-symbol="GONE"' not in html and "No stocks found matching" in html


def test_administrators_can_use_the_stocks_pages(db, client, clocked):  # R2, D19
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    login(client, "admin@example.test", PASSWORD)
    make_stock(db, symbol="AURA")
    assert client.get("/stocks").status_code == 200
    assert client.get("/stocks/AURA").status_code == 200


def test_stocks_pages_need_login(web_app):
    for path in ("/stocks", "/stocks/AURA"):
        assert web_app.test_client().get(path).headers["Location"].startswith("/signin")


def test_stale_prices_banner_on_the_stocks_page(db, signed_in):  # R7.8, A17
    stock(db, "AURA", "40.00", "42.00")
    assert "Prices may be delayed" in signed_in.get("/stocks").get_data(as_text=True)  # no tick yet, market open
    at = (NOW - timedelta(seconds=60)).astimezone(timezone.utc).replace(tzinfo=None)
    one(db, "INSERT INTO SystemState (state_key, state_value, updated_at) VALUES ('last_price_tick', %s, %s)",
        (at.isoformat(), at))
    assert "Prices may be delayed" not in signed_in.get("/stocks/AURA").get_data(as_text=True)
