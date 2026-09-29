"""End-to-end money scenario, every value computed by hand (task 5.2; R8, R9, R11, R12, R17.3, Calculations).

Everything goes through the real pages (Transfer Funds, the Trade ticket's preview and confirm), and
prices move the way the generator moves them (Issuer.current_price). After every step the test checks
the cash balance; each position's quantity, average cost, market value, and gain/loss; the Summary and
Positions totals; and the History rows. The same table is in docs/test-report.md.

Hand calculations (money rounds half-up to the cent; Dollars quantities round down to 6 places):

 # | Step                                    | Cash      | Positions
 1 | deposit $1,000.00                       | 1,000.00  | -
 2 | buy $250.00 of X at $33.33 (Dollars)    |   750.00  | X 7.500750 (250 / 33.33 = 7.50075007.. -> 7.500750);
   |   total 7.500750 x 33.33 = 249.9999975  |           |   avg 33.33, cost basis 250.00
   |   -> $250.00                            |           |
 3 | buy 2.5 shares of Y at $120.10 (Shares) |   449.75  | + Y 2.5, avg 120.10, total 300.25
 4 | prices: X -> $40.00, Y -> $110.00       |   449.75  | X value 7.500750 x 40 = 300.03, gain 300.03 - 250.00 = +50.03
   |   (previous closes 33.33 and 120.10)    |           | Y value 275.00, gain 275.00 - 300.25 = -25.25
   |                                         |           | total 449.75 + 575.03 = 1,024.78
   |                                         |           | day X 7.500750 x 6.67 = 50.0300025 -> 50.03; Y 2.5 x -10.10 = -25.25
   |                                         |           |   = +24.78, on 1,000.00 at the close = +2.48%
   |                                         |           | YTD 1,024.78 - 0 - 1,000.00 = +24.78 (+2.48%)
   |                                         |           | cost 250.00 + 300.25 = 550.25; gain 575.03 - 550.25 = +24.78 (+4.50%)
 5 | sell 3.750375 X (half) at $40.00        |   599.77  | total 150.015 -> 150.02; X 3.750375 left, avg still 33.33
   |                                         |           | X value 150.015 -> 150.02, cost 3.750375 x 33.33 = 124.99999875 -> 125.00
 6 | withdraw $100.00                        |   499.77  | total 499.77 + 150.02 + 275.00 = 924.79
   |                                         |           | day X 3.750375 x 6.67 = 25.01500125 -> 25.02; Y -25.25 = -0.23
   |                                         |           |   on 924.79 + 0.23 = 925.02 -> -0.02%
   |                                         |           | YTD 924.79 - 0 - (1,000 - 100) = +24.79, on 900.00 = +2.75%
   |                                         |           | cost 125.00 + 300.25 = 425.25; gain 425.02 - 425.25 = -0.23 (-0.05%)

R17.3 / P1: 1,000.00 - 250.00 - 300.25 + 150.02 - 100.00 = 499.77.
"""
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.crypto import encrypt_field
from app.services import valuation
from tests.factories import make_bank_account, make_portfolio, make_stock
from tests.web import csrf_token, login, make_login_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=AZ)   # a Tuesday, market open
TODAY = date(2026, 9, 29)
PASSWORD = "correct horse battery"


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def rows(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchall()
    db.commit()
    return list(found)


@pytest.fixture
def world(db, client, web_app, fixed_clock):
    fixed_clock(NOW)
    user = make_login_user(db, PASSWORD, email="casey@example.test")
    portfolio = make_portfolio(db, user, account_name="Brokerage")
    with web_app.app_context():
        bank = make_bank_account(db, user, nickname="Checking", account_number=encrypt_field("000123456789"))
    x = make_stock(db, symbol="XXX", price=D("33.33"), shares_available=D("1000"))
    y = make_stock(db, symbol="YYY", price=D("120.10"), shares_available=D("1000"))
    for issuer, close in ((x, "33.33"), (y, "120.10")):
        one(db, "INSERT INTO StockPrice (issuerID, price_date, open_price, close_price) VALUES (%s, '2026-09-28', %s, %s)",
            (issuer, D(close), D(close)))
    login(client, "casey@example.test", PASSWORD)
    return type("World", (), {"user": user, "portfolio": portfolio, "bank": bank, "x": x, "y": y, "client": client,
                              "app": web_app})


# --- Doing each step through the pages ----------------------------------------------------------------------

def transfer(w, source, target, amount):
    response = w.client.post("/transfer", data={"from_account": source, "to_account": target, "amount": amount,
                                               "transfer_date": TODAY.isoformat(), "csrf_token": csrf_token(w.client, "/transfer")})
    assert response.status_code == 302, response.get_data(as_text=True)[:500]


def trade(w, symbol, action, unit, amount):
    preview = w.client.post("/trade/preview", data={
        "portfolio": w.portfolio, "symbol": symbol, "action": action, "unit": unit, "amount": amount,
        "order_type": "Market", "timing": "Day Only", "csrf_token": csrf_token(w.client, "/trade")})
    nonce = re.search(r'name="nonce" value="([^"]+)"', preview.get_data(as_text=True)).group(1)
    done = w.client.post("/trade/confirm", data={"nonce": nonce, "csrf_token": csrf_token(w.client, "/trade")})
    assert "Order Filled" in done.get_data(as_text=True)


def set_price(db, issuer, price):
    one(db, "UPDATE Issuer SET current_price = %s WHERE issuerID = %s", (D(price), issuer))


# --- Checking every value ------------------------------------------------------------------------------------

def check(db, w, *, cash, positions, totals=None, history):
    """positions: {symbol: (quantity, average_cost, market_value, gain_loss)}."""
    assert one(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (w.portfolio,))["cash_balance"] == D(cash)
    with w.app.app_context():
        view = valuation.positions(w.user, w.portfolio)
        summary = valuation.account_summary(w.user)
    got = {p.symbol: (p.quantity, p.average_cost, p.market_value, p.gain_loss) for p in view.positions}
    assert got == {s: tuple(D(v) for v in values) for s, values in positions.items()}
    held = {r["ticker_symbol"]: r["quantity"] for r in rows(db, "SELECT i.ticker_symbol, inv.quantity FROM Inventory inv"
                                                              " JOIN Issuer i ON i.issuerID = inv.issuerID")}
    assert held == {s: D(v[0]) for s, v in positions.items()}   # the stored rows agree (P3)
    if totals:
        t = view.totals
        got_totals = {"total": t.total_value, "day": t.day_change, "day_pct": t.day_change_pct, "ytd": t.ytd_change,
                      "ytd_pct": t.ytd_change_pct, "cost": t.cost_basis, "gain": t.gain_loss, "gain_pct": t.gain_loss_pct}
        assert got_totals == {k: D(v) for k, v in totals.items()}
        assert summary.totals.total_value == t.total_value and summary.totals.cash == D(cash)   # Summary agrees
    recorded = rows(db, "SELECT h.action, COALESCE(t.total_amount, tr.amount) AS amount, t.quantity, t.price_per_share"
                        " FROM History h LEFT JOIN `Transaction` t ON t.transactionID = h.transactionID"
                        " LEFT JOIN Transfer tr ON tr.transferID = h.transferID ORDER BY h.historyID")
    assert [(r["action"], r["amount"], r["quantity"], r["price_per_share"]) for r in recorded] == [
        (a, D(amt), D(q) if q else None, D(p) if p else None) for a, amt, q, p in history]


def test_the_whole_scenario_matches_the_hand_calculations(db, world):  # R8, R9, R11, R12, R17.3, P1
    w = world
    history = []

    transfer(w, f"bank:{w.bank}", f"portfolio:{w.portfolio}", "1000.00")               # 1
    history.append(("Deposit", "1000.00", None, None))
    check(db, w, cash="1000.00", positions={}, history=history)

    trade(w, "XXX", "Buy", "Dollars", "250.00")                                        # 2
    history.append(("Buy", "250.00", "7.500750", "33.33"))
    check(db, w, cash="750.00", positions={"XXX": ("7.500750", "33.33", "250.00", "0.00")}, history=history)

    trade(w, "YYY", "Buy", "Shares", "2.5")                                            # 3
    history.append(("Buy", "300.25", "2.5", "120.10"))
    check(db, w, cash="449.75", positions={"XXX": ("7.500750", "33.33", "250.00", "0.00"),
                                           "YYY": ("2.5", "120.10", "300.25", "0.00")}, history=history)

    set_price(db, w.x, "40.00")                                                        # 4
    set_price(db, w.y, "110.00")
    check(db, w, cash="449.75",
          positions={"XXX": ("7.500750", "33.33", "300.03", "50.03"), "YYY": ("2.5", "120.10", "275.00", "-25.25")},
          totals={"total": "1024.78", "day": "24.78", "day_pct": "2.48", "ytd": "24.78", "ytd_pct": "2.48",
                  "cost": "550.25", "gain": "24.78", "gain_pct": "4.50"},
          history=history)

    trade(w, "XXX", "Sell", "Shares", "3.750375")                                      # 5
    history.append(("Sell", "150.02", "3.750375", "40.00"))
    check(db, w, cash="599.77",
          positions={"XXX": ("3.750375", "33.33", "150.02", "25.02"), "YYY": ("2.5", "120.10", "275.00", "-25.25")},
          history=history)

    transfer(w, f"portfolio:{w.portfolio}", f"bank:{w.bank}", "100.00")                # 6
    history.append(("Withdrawal", "100.00", None, None))
    check(db, w, cash="499.77",
          positions={"XXX": ("3.750375", "33.33", "150.02", "25.02"), "YYY": ("2.5", "120.10", "275.00", "-25.25")},
          totals={"total": "924.79", "day": "-0.23", "day_pct": "-0.02", "ytd": "24.79", "ytd_pct": "2.75",
                  "cost": "425.25", "gain": "-0.23", "gain_pct": "-0.05"},
          history=history)

    # R17.3 / P1: the cash balance equals money in minus money out, from the records alone.
    money = one(db, "SELECT"
                    " (SELECT COALESCE(SUM(amount), 0) FROM Transfer WHERE transfer_type = 'Deposit') AS deposits,"
                    " (SELECT COALESCE(SUM(amount), 0) FROM Transfer WHERE transfer_type = 'Withdrawal') AS withdrawals,"
                    " (SELECT COALESCE(SUM(total_amount), 0) FROM `Transaction` WHERE type = 'Buy') AS buys,"
                    " (SELECT COALESCE(SUM(total_amount), 0) FROM `Transaction` WHERE type = 'Sell') AS sells")
    assert money["deposits"] - money["withdrawals"] - money["buys"] + money["sells"] == D("499.77")
    # Share supply is conserved (P18): 1,000 each, minus what the customer holds.
    supply = {r["ticker_symbol"]: r["shares_available"] for r in rows(db, "SELECT ticker_symbol, shares_available FROM Issuer")}
    assert supply == {"XXX": D("1000") - D("3.750375"), "YYY": D("1000") - D("2.5")}


def test_the_pages_show_the_same_numbers(db, world):  # R5, R6, R13
    w = world
    transfer(w, f"bank:{w.bank}", f"portfolio:{w.portfolio}", "1000.00")
    trade(w, "XXX", "Buy", "Dollars", "250.00")
    trade(w, "YYY", "Buy", "Shares", "2.5")
    set_price(db, w.x, "40.00")
    set_price(db, w.y, "110.00")
    positions = w.client.get(f"/positions?portfolio={w.portfolio}").get_data(as_text=True)
    for text in ("$1,024.78", "+$24.78 (+2.48%)", "$550.25", "+$24.78 (+4.50%)", "7.50075", "$300.03", "+$50.03", "-$25.25"):
        assert text in positions, text
    summary = w.client.get("/account").get_data(as_text=True)
    assert "$1,024.78" in summary and "$449.75" in summary
    history = w.client.get(f"/history?portfolio={w.portfolio}").get_data(as_text=True)
    for text in ("Bought 7.50075 XXX @ $33.33", "-$250.00", "Bought 2.5 YYY @ $120.10", "-$300.25", "+$1,000.00"):
        assert text in history, text
