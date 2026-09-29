"""Stocks service (task 2.3; R7.1-R7.2, R14.2-R14.6, R16.7, P15, A9, A15)."""
import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import holidays
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.errors import DuplicateSymbol, Forbidden, NotFound, SymbolNotFound, ValidationFailed
from app.services import stocks
from app.services.pricing import previous_close
from tests.factories import make_portfolio, make_stock, make_user
from tests.web import make_login_user

pytestmark = pytest.mark.db

AZ = ZoneInfo("America/Phoenix")
TODAY = date(2026, 9, 28)  # a Monday


def rows(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchall()
    db.commit()
    return found


@pytest.fixture
def admin(db, app_ctx, fixed_clock):
    fixed_clock(datetime(2026, 9, 28, 10, 0, tzinfo=AZ))
    return make_login_user(db, admin=True)


def add(admin, symbol="ACME", price="100.00", randomize=False, seed=1, shares="1000", **kwargs):
    values = {"company_name": "Acme Corp", "description": None, "exchange": None}
    values.update(kwargs)
    return stocks.add_stock(admin, values["company_name"], symbol, Decimal(price), values["description"],
                            values["exchange"], randomize, Decimal(shares), random.Random(seed))


# --- add_stock (R14) ------------------------------------------------------------------------

def test_stock_is_created_at_the_entered_price(db, admin):  # R14.5, SR01
    issuer_id = add(admin, symbol=" acme ", company_name="  Acme Corp ", exchange="NASDAQ", description="Road runner supplies")
    stock = stocks.get_stock("ACME")
    assert stock.issuer_id == issuer_id
    assert (stock.symbol, stock.company_name, stock.exchange) == ("ACME", "Acme Corp", "NASDAQ")
    assert stock.current_price == Decimal("100.00") and isinstance(stock.current_price, Decimal)


@pytest.mark.parametrize("field, overrides", [
    ("company_name", {"company_name": ""}),
    ("company_name", {"company_name": "x" * 256}),
    ("symbol", {"symbol": ""}),
    ("symbol", {"symbol": "AB-C"}),
    ("symbol", {"symbol": "ABCDEFGHIJK"}),
    ("symbol", {"symbol": "ÄBC"}),
    ("symbol", {"symbol": "A B"}),
    ("price", {"price": "0"}),
    ("price", {"price": "0.00"}),
    ("price", {"price": "-5.00"}),
    ("price", {"price": "10.005"}),
    ("price", {"price": "NaN"}),
    ("price", {"price": "100000000.00"}),
    ("description", {"description": "x" * 51}),  # A15: 50-character limit
    ("exchange", {"exchange": "x" * 11}),
])
def test_invalid_stocks_are_rejected_and_nothing_is_saved(db, admin, field, overrides):  # R14.2, R14.3
    values = {"symbol": "OK", "price": "10.00"}
    values.update(overrides)
    with pytest.raises(ValidationFailed) as exc:
        add(admin, **values)
    assert exc.value.field == field
    assert rows(db, "SELECT COUNT(*) AS n FROM Issuer")[0]["n"] == 0
    assert rows(db, "SELECT COUNT(*) AS n FROM StockPrice")[0]["n"] == 0


def test_price_must_be_a_decimal_not_a_float(admin):  # N4.1
    with pytest.raises(ValidationFailed):
        stocks.add_stock(admin, "Acme", "ACME", 10.5, None, None, False, Decimal("100"))


def test_duplicate_symbol_is_rejected_whatever_its_case(db, admin):  # R14.4
    add(admin, symbol="ACME")
    with pytest.raises(DuplicateSymbol) as exc:
        add(admin, symbol="acme", randomize=True)
    assert exc.value.message == "Symbol already exists"
    assert rows(db, "SELECT COUNT(*) AS n FROM Issuer")[0]["n"] == 1
    assert rows(db, "SELECT COUNT(*) AS n FROM StockPrice")[0]["n"] == 1  # the second stock's history was rolled back


def test_only_administrators_can_add_stocks(db, admin):  # R14.1
    customer = make_login_user(db)
    with pytest.raises(Forbidden):
        add(customer)


# --- price history (R14.6, R16.7, A9) --------------------------------------------------------

def expected_trading_days(today):
    nyse = holidays.NYSE(years=[today.year - 1, today.year])
    days = [today - timedelta(days=n) for n in range(365, 0, -1)]
    return [d for d in days if d.weekday() < 5 and d not in nyse]


def test_without_randomize_only_todays_price_row_exists(db, admin):  # A9
    issuer_id = add(admin, price="42.50")
    history = rows(db, "SELECT price_date, open_price, close_price FROM StockPrice WHERE issuerID = %s", (issuer_id,))
    assert history == [{"price_date": TODAY, "open_price": Decimal("42.50"), "close_price": Decimal("42.50")}]


def test_randomized_history_covers_a_year_of_trading_days(db, admin):  # R14.6, R16.7, A9
    issuer_id = add(admin, price="42.50", randomize=True)
    history = rows(db, "SELECT price_date, open_price, close_price FROM StockPrice WHERE issuerID = %s"
                       " ORDER BY price_date", (issuer_id,))
    dates = [h["price_date"] for h in history]
    assert dates == expected_trading_days(TODAY) + [TODAY]  # one row per trading day; weekends and holidays skipped
    assert 245 <= len(dates) <= 255
    assert all(d.weekday() < 5 for d in dates)
    for earlier, later in zip(history, history[1:]):
        assert later["open_price"] == earlier["close_price"]  # each day opens at the previous close
    assert history[-2]["close_price"] == Decimal("42.50")      # the history ends at the entered price
    assert history[-1]["open_price"] == history[-1]["close_price"] == Decimal("42.50")
    for h in history:
        for price in (h["open_price"], h["close_price"]):
            assert price >= Decimal("0.01") and price == price.quantize(Decimal("0.01"))


def test_randomized_history_is_repeatable_with_the_same_seed(db, admin):
    first = add(admin, symbol="ONE", randomize=True, seed=7)
    second = add(admin, symbol="TWO", randomize=True, seed=7)
    closes = lambda issuer: [r["close_price"] for r in rows(
        db, "SELECT close_price FROM StockPrice WHERE issuerID = %s ORDER BY price_date", (issuer,))]
    assert closes(first) == closes(second)


def test_previous_close_is_the_last_close_before_today(db, admin):  # A8
    issuer_id = add(admin, price="42.50", randomize=True)
    last_past = rows(db, "SELECT close_price FROM StockPrice WHERE issuerID = %s AND price_date < %s"
                         " ORDER BY price_date DESC LIMIT 1", (issuer_id, TODAY))[0]["close_price"]
    assert previous_close(issuer_id, TODAY) == last_past == Decimal("42.50")


def test_previous_close_falls_back_to_the_current_price(db, admin):
    issuer_id = make_stock(db, symbol="NEW", price=Decimal("12.34"))
    assert previous_close(issuer_id, TODAY) == Decimal("12.34")


# --- search (R7.1, R7.2, P15) -------------------------------------------------------------------

@pytest.fixture
def catalog(db, admin):
    for symbol, name in [("AAPL", "Apple Inc"), ("MSFT", "Microsoft Corp"), ("APX", "100% Apex Fund"),
                         ("UND", "Under_Score Ltd"), ("ZED", "Zed Holdings")]:
        make_stock(db, symbol=symbol, company_name=name)


@pytest.mark.parametrize("query, expected", [
    ("AAPL", ["AAPL"]),
    ("aapl", ["AAPL"]),            # case-insensitive
    ("apple", ["AAPL"]),           # company name
    ("  corp ", ["MSFT"]),         # trimmed
    ("ap", ["AAPL", "APX"]),       # symbol or name contains it; ordered by symbol
    ("%", ["APX"]),                # wildcard characters match literally
    ("_", ["UND"]),
    ("100%", ["APX"]),
    ("zz", []),
])
def test_search(catalog, query, expected):  # R7.1, P15
    assert [s.symbol for s in stocks.search_stocks(query)] == expected


@pytest.mark.parametrize("query", ["", "   ", None])
def test_empty_search_returns_nothing(catalog, query):
    assert stocks.search_stocks(query) == []


def test_overlong_search_is_rejected(catalog):
    with pytest.raises(ValidationFailed):
        stocks.search_stocks("x" * 101)


def test_search_input_cannot_inject_sql(catalog, db):  # N2.1
    assert stocks.search_stocks("' OR '1'='1") == []
    assert stocks.search_stocks("'; DROP TABLE Issuer; --") == []
    assert rows(db, "SELECT COUNT(*) AS n FROM Issuer")[0]["n"] == 5


SYMBOLS = st.text(alphabet="ABCXYZ019", min_size=1, max_size=4)
NAMES = st.text(alphabet="abc XYZ%_|19", min_size=1, max_size=12).filter(lambda s: s.strip())
QUERIES = st.text(alphabet="abcxyzABC19 %_|", min_size=1, max_size=4).filter(lambda s: s.strip())


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(catalog=st.dictionaries(SYMBOLS, NAMES, min_size=0, max_size=6), query=QUERIES)
def test_p15_search_returns_exactly_the_matching_stocks(db, app_ctx, catalog, query):  # P15
    with db.cursor() as cur:  # fresh catalog for every generated example
        cur.execute("DELETE FROM StockPrice")
        cur.execute("DELETE FROM Issuer")
    db.commit()
    for symbol, name in catalog.items():
        make_stock(db, symbol=symbol, company_name=name.strip())
    q = query.strip().lower()
    expected = sorted(s for s, n in catalog.items() if q in s.lower() or q in n.strip().lower())
    assert [s.symbol for s in stocks.search_stocks(query)] == expected


# --- get_stock (R8.2) -----------------------------------------------------------------------------

def test_get_stock_accepts_any_case(catalog):
    assert stocks.get_stock("msft").company_name == "Microsoft Corp"


@pytest.mark.parametrize("symbol", ["NOPE", "", None, "'; DROP TABLE Issuer; --", "x" * 50])
def test_unknown_or_malformed_symbols_are_not_found(catalog, symbol):
    with pytest.raises(SymbolNotFound) as exc:
        stocks.get_stock(symbol)
    assert exc.value.message == "Symbol not found"


# --- Share supply (D16, AR03) --------------------------------------------------------------------------

def test_stock_is_created_with_its_share_supply(admin):  # AR03, D16
    add(admin, shares="2500.5")
    stock = stocks.get_stock("ACME")
    assert stock.shares_available == Decimal("2500.5") and stock.is_listed


@pytest.mark.parametrize("shares", ["0", "-1", "1.0000001", "NaN", "1000000000000"])
def test_invalid_share_supplies_are_rejected(db, admin, shares):  # R14.2
    with pytest.raises(ValidationFailed) as exc:
        add(admin, shares=shares)
    assert exc.value.field == "shares"
    assert rows(db, "SELECT COUNT(*) AS n FROM Issuer")[0]["n"] == 0


def test_share_supply_must_be_a_decimal(admin):
    with pytest.raises(ValidationFailed):
        stocks.add_stock(admin, "Acme", "ACME", Decimal("10.00"), None, None, False, 100.0)


def test_database_refuses_a_negative_share_supply(db, admin):  # DV9
    import pymysql

    issuer_id = make_stock(db, symbol="NEG")
    with pytest.raises(pymysql.err.OperationalError) as exc:
        with db.cursor() as cur:
            cur.execute("UPDATE Issuer SET shares_available = -1 WHERE issuerID = %s", (issuer_id,))
    db.rollback()
    assert exc.value.args[0] == 3819


# --- Editing (R14.8, D20) --------------------------------------------------------------------------------

def test_admin_edits_name_description_and_exchange_only(admin):  # R14.8
    issuer_id = add(admin, symbol="ACME", price="42.00", company_name="Acme")
    edited = stocks.update_stock(admin, issuer_id, "  Acme Holdings ", "New description", "NYSE")
    assert (edited.company_name, edited.description, edited.exchange) == ("Acme Holdings", "New description", "NYSE")
    assert edited.symbol == "ACME" and edited.current_price == Decimal("42.00")  # not editable


@pytest.mark.parametrize("field, name, description, exchange", [
    ("company_name", "", None, None), ("description", "Acme", "x" * 51, None), ("exchange", "Acme", None, "x" * 11),
])
def test_invalid_edits_are_rejected(admin, field, name, description, exchange):
    issuer_id = add(admin)
    with pytest.raises(ValidationFailed) as exc:
        stocks.update_stock(admin, issuer_id, name, description, exchange)
    assert exc.value.field == field
    assert stocks.get_stock("ACME").company_name == "Acme Corp"


def test_editing_needs_an_admin_and_an_existing_stock(db, admin):
    issuer_id = add(admin)
    with pytest.raises(Forbidden):
        stocks.update_stock(make_login_user(db), issuer_id, "X", None, None)
    with pytest.raises(NotFound):
        stocks.update_stock(admin, 999999, "X", None, None)


# --- Delisting (R14.9, R7.9, D20) ---------------------------------------------------------------------------

def test_delisted_stocks_leave_search_and_buying_but_not_holdings(db, admin):  # R14.9, R7.9, R9.10
    issuer_id = add(admin, symbol="GONE", company_name="Gone Corp")
    owner = make_user(db)
    portfolio = make_portfolio(db, owner)
    with db.cursor() as cur:
        cur.execute("INSERT INTO Inventory (portfolioID, issuerID, quantity, average_cost) VALUES (%s, %s, 5, 10.00)",
                    (portfolio, issuer_id))
    db.commit()

    stocks.delist_stock(admin, issuer_id)

    assert stocks.search_stocks("gone") == []
    with pytest.raises(SymbolNotFound):
        stocks.get_stock("GONE")                                               # can't be bought
    assert stocks.get_stock("GONE", include_delisted=True).is_listed is False  # holders can still sell
    assert rows(db, "SELECT quantity FROM Inventory WHERE issuerID = %s", (issuer_id,))[0]["quantity"] == Decimal("5")


def test_delisting_needs_an_admin_and_an_existing_stock(db, admin):
    issuer_id = add(admin)
    with pytest.raises(Forbidden):
        stocks.delist_stock(make_login_user(db), issuer_id)
    with pytest.raises(NotFound):
        stocks.delist_stock(admin, 999999)
    assert stocks.get_stock("ACME").is_listed


def test_limits_are_stated_in_the_messages(admin):
    with pytest.raises(ValidationFailed, match=r"The largest amount you can enter is \$99,999,999\.99\."):
        add(admin, price="100000000.00")
    with pytest.raises(ValidationFailed, match=r"999,999,999,999\.999999"):
        add(admin, shares="1000000000000")
