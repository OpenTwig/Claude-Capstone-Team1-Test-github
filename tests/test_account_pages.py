"""Summary, Create Portfolio, and Positions (task 4.2; wf-3, wf-4; R4, R5, R6, D17, D22, D23, D26).

The golden case below was worked out by hand (Calculations table, D26):

Stock X: previous close $330.00, now $336.79 -> change +$6.79, +2.06%.
Stock Y: previous close $50.00, now $45.50 -> change -$4.50, -9.00%.

Portfolio A, cash $1,000.00
  X 1.592 @ avg $131.11: value 1.592 x 336.79 = 536.16968 -> $536.17; day 1.592 x 6.79 = 10.80968 -> $10.81;
                         cost 1.592 x 131.11 = 208.72712 -> $208.73; gain $327.44
  Y 10 @ avg $60.00:     value $455.00; day -$45.00; cost $600.00; gain -$145.00
  positions $991.17, total $1,991.17, day -$34.19 (-34.19 / 2,025.36 = -1.69%)
  Equities Total row (stocks only): day -34.19 / (991.17 + 34.19) = -3.33%
  cost $808.73, gain $182.44 (182.44 / 808.73 = 22.56%)
  start of year: last 2025 snapshot $1,500.00; money in this year +500 deposit -200 withdrawal -100 moved to B = $200.00
  YTD 1,991.17 - 1,500.00 - 200.00 = $291.17 (291.17 / 1,700.00 = 17.13%)

Portfolio B (opened this year, no snapshot before it), cash $100.00
  X 2 @ avg $300.00: value $673.58; day $13.58; cost $600.00; gain $73.58 (12.26%)
  total $773.58, day 13.58 / 760.00 = 1.79%; money in: $100.00 moved from A; YTD 773.58 - 0 - 100 = $673.58 (673.58%)

All portfolios: cash $1,100.00, total $2,764.75, day -$20.61 (-20.61 / 2,785.36 = -0.74%),
  YTD $964.75 on a base of 1,500 + 200 + 0 + 100 = $1,800.00 (53.60%): the move between A and B cancels out.
"""
import json
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.errors import NotFound
from app.services import portfolios, valuation
from tests.factories import make_bank_account, make_portfolio, make_stock, make_user
from tests.web import csrf_token, login, make_login_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=AZ)
TODAY = date(2026, 9, 28)
PASSWORD = "correct horse battery"


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def hold(db, portfolio_id, issuer_id, quantity, average, reinvest=False):
    one(db, "INSERT INTO Inventory (portfolioID, issuerID, quantity, average_cost, reinvest) VALUES (%s, %s, %s, %s, %s)",
        (portfolio_id, issuer_id, D(quantity), D(average), reinvest))


def snapshot(db, portfolio_id, day, total, cash="0.00"):
    one(db, "INSERT INTO PortfolioValueDaily (portfolioID, value_date, cash, total_value) VALUES (%s, %s, %s, %s)",
        (portfolio_id, day, D(cash), D(total)))


def transfer(db, kind, amount, day, *, to=None, source=None, bank=None, status="Completed"):
    one(db, "INSERT INTO Transfer (from_portfolioID, to_portfolioID, bankAccountID, amount, transfer_date, status,"
            " transfer_type) VALUES (%s, %s, %s, %s, %s, %s, %s)", (source, to, bank, D(amount), day, status, kind))


def stock(db, symbol, previous, current, **kwargs):
    issuer = make_stock(db, symbol=symbol, price=D(current), **kwargs)
    one(db, "INSERT INTO StockPrice (issuerID, price_date, open_price, close_price) VALUES (%s, %s, %s, %s)",
        (issuer, date(2026, 9, 25), D(previous), D(previous)))
    return issuer


@pytest.fixture
def golden(db, fixed_clock):
    fixed_clock(NOW)
    user = make_login_user(db, PASSWORD, email="casey@example.test")
    x = stock(db, "XXX", "330.00", "336.79", company_name="X Corp")
    y = stock(db, "YYY", "50.00", "45.50", company_name="Y Corp")
    a = make_portfolio(db, user, account_name="Growth", cash_balance=D("1000.00"))
    b = make_portfolio(db, user, account_name="Income", cash_balance=D("100.00"))
    hold(db, a, x, "1.592", "131.11")
    hold(db, a, y, "10", "60.00")
    hold(db, b, x, "2", "300.00")
    snapshot(db, a, date(2025, 6, 1), "1200.00")      # older than a year: not charted
    snapshot(db, a, date(2025, 12, 31), "1500.00")    # the start-of-year value
    snapshot(db, a, date(2026, 1, 5), "1600.00")
    snapshot(db, b, date(2026, 1, 5), "50.00")
    snapshot(db, a, TODAY, "9999.00")                 # replaced by the live value
    bank = make_bank_account(db, user)
    transfer(db, "Deposit", "300.00", date(2025, 12, 15), to=a, bank=bank)   # last year: ignored
    transfer(db, "Deposit", "500.00", date(2026, 2, 1), to=a, bank=bank)
    transfer(db, "Withdrawal", "200.00", date(2026, 3, 1), source=a, bank=bank)
    transfer(db, "Internal", "100.00", date(2026, 4, 1), source=a, to=b)
    transfer(db, "Deposit", "999.00", date(2026, 5, 1), to=a, bank=bank, status="Pending")  # never completed
    other = make_portfolio(db, make_user(db), cash_balance=D("777.00"))   # someone else's: never counted
    hold(db, other, x, "5", "1.00")
    return type("Golden", (), {"user": user, "a": a, "b": b, "x": x, "y": y, "other": other})


# --- Valuation: the golden case (Calculations table, D23, D26) --------------------------------------

def test_positions_match_the_hand_computed_values(db, app_ctx, golden):  # R6.2, R6.3
    view = valuation.positions(golden.user, golden.a)
    x, y = view.positions
    assert (x.symbol, x.quantity, x.price, x.price_change, x.price_change_pct) == ("XXX", D("1.592"), D("336.79"), D("6.79"), D("2.06"))
    assert (x.market_value, x.day_change, x.cost_basis, x.gain_loss) == (D("536.17"), D("10.81"), D("208.73"), D("327.44"))
    assert (y.price_change, y.price_change_pct, y.market_value, y.day_change, y.cost_basis, y.gain_loss) \
        == (D("-4.50"), D("-9.00"), D("455.00"), D("-45.00"), D("600.00"), D("-145.00"))
    t = view.totals
    assert (t.cash, t.positions_value, t.total_value) == (D("1000.00"), D("991.17"), D("1991.17"))
    assert (t.day_change, t.day_change_pct) == (D("-34.19"), D("-1.69"))
    assert (t.ytd_change, t.ytd_change_pct) == (D("291.17"), D("17.13"))
    assert (t.cost_basis, t.gain_loss, t.gain_loss_pct) == (D("808.73"), D("182.44"), D("22.56"))
    assert (view.equities.positions_value, view.equities.day_change, view.equities.cost_basis, view.equities.gain_loss) \
        == (D("991.17"), D("-34.19"), D("808.73"), D("182.44"))
    assert view.equities.cash == D("0.00") and view.equities.day_change_pct == D("-3.33")  # stocks only


def test_a_portfolio_opened_this_year_starts_from_zero(db, app_ctx, golden):  # D23
    t = valuation.positions(golden.user, golden.b).totals
    assert (t.total_value, t.day_change, t.day_change_pct) == (D("773.58"), D("13.58"), D("1.79"))
    assert (t.ytd_change, t.ytd_change_pct) == (D("673.58"), D("673.58"))
    assert (t.gain_loss, t.gain_loss_pct) == (D("73.58"), D("12.26"))


def test_summary_totals_across_portfolios(db, app_ctx, golden):  # R5.1, R5.3, R5.4
    summary = valuation.account_summary(golden.user)
    assert [r.portfolio.account_name for r in summary.rows] == ["Growth", "Income"]
    assert [r.totals.total_value for r in summary.rows] == [D("1991.17"), D("773.58")]
    t = summary.totals
    assert (t.cash, t.total_value, t.day_change, t.day_change_pct) == (D("1100.00"), D("2764.75"), D("-20.61"), D("-0.74"))
    assert (t.ytd_change, t.ytd_change_pct) == (D("964.75"), D("53.60"))


def test_the_chart_uses_the_last_year_of_snapshots_and_todays_live_value(db, app_ctx, golden):  # R5.2, D23
    assert valuation.account_summary(golden.user).chart == (
        (date(2025, 12, 31), D("1500.00")), (date(2026, 1, 5), D("1650.00")), (TODAY, D("2764.75")))
    assert valuation.positions(golden.user, golden.b).chart == ((date(2026, 1, 5), D("50.00")), (TODAY, D("773.58")))


def test_deposits_are_not_counted_as_gains(db, app_ctx, fixed_clock):  # D23: YTD excludes deposits
    fixed_clock(NOW)
    user = make_user(db)
    portfolio = make_portfolio(db, user, cash_balance=D("25000.00"))
    transfer(db, "Deposit", "25000.00", TODAY, to=portfolio, bank=make_bank_account(db, user))
    t = valuation.positions(user, portfolio).totals
    assert (t.total_value, t.ytd_change, t.ytd_change_pct) == (D("25000.00"), D("0.00"), D("0.00"))


def test_empty_portfolio_percentages_are_zero(db, app_ctx, fixed_clock):  # D26: no base, no percentage
    fixed_clock(NOW)
    user = make_user(db)
    t = valuation.positions(user, make_portfolio(db, user)).totals
    assert (t.total_value, t.day_change_pct, t.ytd_change_pct, t.gain_loss_pct) == (D("0.00"), D("0.00"), D("0.00"), D("0.00"))


def test_delisted_holdings_are_still_valued(db, app_ctx, fixed_clock):  # D20
    fixed_clock(NOW)
    user = make_user(db)
    portfolio = make_portfolio(db, user)
    gone = stock(db, "GONE", "5.00", "4.00", is_listed=False)
    hold(db, portfolio, gone, "3", "5.00")
    [row] = valuation.positions(user, portfolio).positions
    assert (row.symbol, row.is_listed, row.market_value, row.gain_loss) == ("GONE", False, D("12.00"), D("-3.00"))


def test_someone_elses_portfolio_is_not_found(db, app_ctx, golden):  # R2.5
    with pytest.raises(NotFound):
        valuation.positions(golden.user, golden.other)


def test_no_portfolios_means_an_empty_summary(db, app_ctx, fixed_clock):  # R5.6
    fixed_clock(NOW)
    summary = valuation.account_summary(make_user(db))
    assert summary.rows == () and summary.totals.total_value == D("0.00")


# --- Reinvest? (R6.7, D22) -----------------------------------------------------------------------------

def test_reinvest_saves_the_flag_and_nothing_else(db, app_ctx, golden):  # R6.7, D22
    before = one(db, "SELECT quantity, average_cost FROM Inventory WHERE portfolioID = %s AND issuerID = %s", (golden.a, golden.x))
    cash = one(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (golden.a,))["cash_balance"]
    portfolios.set_reinvest(golden.user, golden.a, golden.x, True)
    after = one(db, "SELECT quantity, average_cost, reinvest FROM Inventory WHERE portfolioID = %s AND issuerID = %s",
                (golden.a, golden.x))
    assert after["reinvest"] == 1 and (after["quantity"], after["average_cost"]) == (before["quantity"], before["average_cost"])
    assert one(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (golden.a,))["cash_balance"] == cash
    assert one(db, "SELECT reinvest FROM Inventory WHERE portfolioID = %s AND issuerID = %s", (golden.a, golden.y))["reinvest"] == 0
    portfolios.set_reinvest(golden.user, golden.a, golden.x, False)
    assert valuation.positions(golden.user, golden.a).positions[0].reinvest is False


@pytest.mark.parametrize("case", ["theirs", "no position"])
def test_reinvest_on_someone_elses_or_a_missing_position_is_not_found(db, app_ctx, golden, case):  # R2.5
    portfolio, issuer = (golden.other, golden.x) if case == "theirs" else (golden.b, golden.y)
    with pytest.raises(NotFound):
        portfolios.set_reinvest(golden.user, portfolio, issuer, True)


# --- Pages ---------------------------------------------------------------------------------------------

@pytest.fixture
def signed_in(db, client, golden):
    login(client, "casey@example.test", PASSWORD)
    return client


def chart_json(html):
    return json.loads(re.search(r'<script type="application/json" id="chart-data">(.*?)</script>', html, re.S).group(1))


def test_summary_page(db, signed_in, golden):  # R5.1-R5.4
    html = signed_in.get("/account").get_data(as_text=True)
    assert "Summary" in html and "Total Value" in html and "More information" in html
    total = html[html.index("data-total-value"):html.index("data-portfolios")]
    assert "$2,764.75" in total and "-$20.61 (-0.74%)" in total and "+$964.75 (+53.60%)" in total
    number = one(db, "SELECT account_number FROM Portfolio WHERE portfolioID = %s", (golden.a,))["account_number"]
    assert f"•••• {number[-4:]}" in html and number not in html                      # R5.3: last 4 only
    for column in ("Account Name", "Type", "Cash", "Account Value", "Day Change", "YTD Change"):
        assert f">{column}</th>" in html, column
    assert "$1,991.17" in html and "+$291.17 (+17.13%)" in html and "+$673.58 (+673.58%)" in html
    footer = html[html.index("data-investments-total"):]
    assert "Investments Total" in footer and "$1,100.00" in footer and "$2,764.75" in footer
    assert chart_json(html)["values"] == ["1500.00", "1650.00", "2764.75"]
    assert '<script src="/static/js/account.js"></script>' in html
    # The whole row opens that portfolio's Positions (user feedback); the name is still a link.
    assert re.search(rf'data-href="/positions\?portfolio={golden.a}" class="clickable-row"', html)
    assert f'href="/positions?portfolio={golden.a}" class="fw-semibold"' in html


def test_summary_search_filters_the_table(db, signed_in):  # R5.5
    html = signed_in.get("/account?q=inc").get_data(as_text=True)
    rows = re.findall(r'data-portfolio-row data-search="(\w+)', html)
    assert rows == ["Income"] and "$2,764.75" in html[html.index("data-investments-total"):]  # totals still cover everything
    none = signed_in.get("/account?q=zzz").get_data(as_text=True)
    assert re.findall(r"data-portfolio-row ", none) == [] and 'No portfolios match "zzz".' in none.replace("&#34;", '"')


def test_summary_without_portfolios_shows_the_create_form(db, client, fixed_clock):  # R5.6, R4.1
    fixed_clock(NOW)
    make_login_user(db, PASSWORD, email="new@example.test")
    login(client, "new@example.test", PASSWORD)
    html = client.get("/account").get_data(as_text=True)
    assert "data-empty" in html and "You don't have a portfolio yet" in html
    assert 'class="collapse show" id="create-portfolio"' in html and "Account Name" in html and "Account Type" in html


def create(client, **fields):
    data = {"account_name": "Retirement", "account_type": "Brokerage", "csrf_token": csrf_token(client, "/account")}
    data.update(fields)
    return client.post("/account/portfolios", data=data)


def test_creating_a_portfolio(db, signed_in, golden):  # R4.2, R4.4, R4.5
    response = create(signed_in)
    assert response.status_code == 302 and response.headers["Location"] == "/account"
    new = one(db, "SELECT account_number, cash_balance FROM Portfolio WHERE userID = %s AND account_name = 'Retirement'",
              (golden.user,))
    assert new["cash_balance"] == D("0.00")
    html = signed_in.get("/account").get_data(as_text=True)
    assert (f"Portfolio &#34;Retirement&#34; created. Account number •••• {new['account_number'][-4:]}." in html
            or f'Portfolio "Retirement" created. Account number •••• {new["account_number"][-4:]}.' in html)
    assert new["account_number"] not in html
    assert "Retirement" in signed_in.get("/positions").get_data(as_text=True)  # in every selector (R4.5)
    assert "Retirement" in signed_in.get("/trade").get_data(as_text=True)


@pytest.mark.parametrize("fields, field", [({"account_name": ""}, "account_name"), ({"account_name": "x" * 101}, "account_name"),
                                           ({"account_type": ""}, "account_type"), ({"account_type": "Margin"}, "account_type")])
def test_create_portfolio_errors(db, signed_in, golden, fields, field):  # R4.2, R4.3
    count = one(db, "SELECT COUNT(*) AS n FROM Portfolio")["n"]
    html = create(signed_in, **fields).get_data(as_text=True)
    assert f'data-error="{field}"' in html and 'class="collapse show" id="create-portfolio"' in html
    assert one(db, "SELECT COUNT(*) AS n FROM Portfolio")["n"] == count


def test_positions_page(db, signed_in, golden):  # R6.1-R6.6, D17
    html = signed_in.get(f"/positions?portfolio={golden.a}").get_data(as_text=True)
    assert "Positions" in html and re.search(rf'value="{golden.a}" selected', html) and "Income" in html  # selector (R6.1)
    value = html[html.index("data-portfolio-value"):html.index("data-position-details")]
    for text in ("$1,991.17", "-$34.19 (-1.69%)", "+$291.17 (+17.13%)", "$808.73", "+$182.44 (+22.56%)"):
        assert text in value, text
    for column in ("Symbol", "Quantity", "Price", "Price Change $", "Price Change %", "Market Value", "Day Change $",
                   "Day Change %", "Cost Basis", "Gain/Loss $", "Reinvest?"):
        assert f">{column}</th>" in html, column
    x_row = html[html.index('data-position="XXX"'):html.index('data-position="YYY"')]
    for text in ("1.592", "$336.79", "+$6.79", "+2.06%", "$536.17", "+$10.81", "$208.73", "+$327.44"):
        assert text in x_row, text
    equities = html[html.index("data-equities-total"):html.index("data-cash-total")]
    assert "$991.17" in equities and "-$34.19" in equities and "-3.33%" in equities and "$808.73" in equities
    cash = html[html.index("data-cash-total"):]
    assert "$1,000.00" in cash and "$0.00" in cash and "0%" in cash and cash.count("N/A") == 2   # R6.5
    assert "Mutual Funds" not in html                                                             # D17
    assert chart_json(html)["values"] == ["1500.00", "1600.00", "1991.17"]


def test_positions_defaults_to_the_first_portfolio(db, signed_in, golden):
    html = signed_in.get("/positions").get_data(as_text=True)
    assert re.search(rf'value="{golden.a}" selected', html)


def test_positions_without_stocks(db, signed_in, golden):
    empty = make_portfolio(db, golden.user, account_name="Empty", cash_balance=D("5.00"))
    html = signed_in.get(f"/positions?portfolio={empty}").get_data(as_text=True)
    assert "data-no-positions" in html and "No stocks yet." in html and "$5.00" in html


def test_positions_without_a_portfolio(db, client, fixed_clock):
    fixed_clock(NOW)
    make_login_user(db, PASSWORD, email="new@example.test")
    login(client, "new@example.test", PASSWORD)
    assert "data-no-portfolio" in client.get("/positions").get_data(as_text=True)


def test_someone_elses_positions_are_a_404(db, signed_in, golden):  # R2.5
    assert signed_in.get(f"/positions?portfolio={golden.other}").status_code == 404
    assert signed_in.get("/positions?portfolio=abc").status_code == 404


def reinvest(client, portfolio, issuer, value, token=True):
    data = {"reinvest": value}
    if token:
        data["csrf_token"] = csrf_token(client, "/account")
    return client.post(f"/positions/{portfolio}/{issuer}/reinvest", data=data)


def test_reinvest_toggle_through_the_page(db, signed_in, golden):  # R6.7, D22
    html = signed_in.get(f"/positions?portfolio={golden.a}").get_data(as_text=True)
    assert 'aria-pressed="false">No</button>' in html
    response = reinvest(signed_in, golden.a, golden.x, "yes")
    assert response.status_code == 302 and response.headers["Location"] == f"/positions?portfolio={golden.a}"
    html = signed_in.get(f"/positions?portfolio={golden.a}").get_data(as_text=True)
    x_row = html[html.index('data-position="XXX"'):html.index('data-position="YYY"')]
    assert 'aria-pressed="true">Yes</button>' in x_row and 'name="reinvest" value="no"' in x_row


def test_reinvest_rejections(db, signed_in, golden):  # R2.5, N2.3
    assert reinvest(signed_in, golden.other, golden.x, "yes").status_code == 404
    assert reinvest(signed_in, golden.b, golden.y, "yes").status_code == 404
    assert reinvest(signed_in, golden.a, golden.x, "maybe").status_code == 400
    assert reinvest(signed_in, golden.a, golden.x, "yes", token=False).status_code == 400
    assert one(db, "SELECT reinvest FROM Inventory WHERE portfolioID = %s AND issuerID = %s", (golden.a, golden.x))["reinvest"] == 0


def test_administrators_get_403(db, client, fixed_clock):  # D19
    fixed_clock(NOW)
    make_login_user(db, PASSWORD, admin=True, email="admin@example.test")
    login(client, "admin@example.test", PASSWORD)
    token = csrf_token(client, "/stocks")
    assert client.get("/account").status_code == 403
    assert client.get("/positions").status_code == 403
    assert client.post("/account/portfolios", data={"csrf_token": token}).status_code == 403
    assert client.post("/positions/1/1/reinvest", data={"csrf_token": token, "reinvest": "yes"}).status_code == 403


def test_pages_need_login(web_app):
    for path in ("/account", "/positions"):
        assert web_app.test_client().get(path).headers["Location"].startswith("/signin")
