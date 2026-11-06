"""The Trade page: look up a stock, fill in the order, review it, then place it. Customers only.

Placing an order takes two steps:

1. /trade/preview checks the order and shows the "Review Your Order" page. Nothing is saved yet;
   the order waits in the session (Flask's per-browser storage).
2. /trade/confirm ("Place order") checks everything again, then saves the trade. Prices move
   all the time, so the order is filled at the price at that moment.
"""
from decimal import ROUND_DOWN, Decimal

from flask import Blueprint, abort, render_template, request, session
from flask_login import current_user

from app import market
from app.auth import customer_required
from app.common import (ARIZONA, UserError, get_portfolio, list_portfolios, money, now_for_database,
                        now_utc, parse_money, parse_shares, percent, to_cents, today)
from app.models import History, Inventory, Issuer, Portfolio, Transaction, db

bp = Blueprint("trade", __name__)

ACTIONS = ["Buy", "Sell"]
UNITS = ["Dollars", "Shares"]  # buy $500 worth, or buy 10 shares
ORDER_TYPES = ["Market"]
TIMINGS = ["Day Only"]


# --- Small helpers ------------------------------------------------------------------------------

ONE_MILLIONTH = Decimal("0.000001")  # the smallest share amount we store


def round_shares_down(quantity):
    """Round a number of shares down to 6 decimal places, so we never sell more than was paid for."""
    return quantity.quantize(ONE_MILLIONTH, rounding=ROUND_DOWN)


def choose_portfolio(user_id, portfolios, portfolio_id_text):
    """The portfolio picked in the URL or form, or else the customer's first one.

    Returns None if the customer has no portfolios.
    """
    if len(portfolios) == 0:
        return None
    if not portfolio_id_text:
        return portfolios[0]
    if not str(portfolio_id_text).isdigit():
        abort(404)
    return get_portfolio(user_id, int(portfolio_id_text))


# --- Looking up a stock ------------------------------------------------------------------------------

def get_quote(user_id, typed_symbol, portfolio_id):
    """The stock's price, its change since yesterday, whether the market is open, and how many
    shares the portfolio owns. Raises UserError("Symbol not found") if there is no such stock."""
    get_portfolio(user_id, portfolio_id)  # 404 if the portfolio isn't theirs
    symbol = (typed_symbol or "").strip().upper()
    stock = Issuer.query.filter_by(symbol=symbol).first()

    shares_owned = Decimal(0)
    if stock is not None:
        holding = Inventory.query.filter_by(portfolio_id=portfolio_id, issuer_id=stock.issuer_id).first()
        if holding is not None:
            shares_owned = holding.quantity

    # No such stock, or a delisted one: "not found" (except to someone who owns it and may sell).
    if stock is None or (not stock.is_listed and shares_owned == 0):
        raise UserError("Symbol not found", "symbol")

    previous_close = stock.previous_close(today())
    change = stock.current_price - previous_close
    return {
        "symbol": stock.symbol,
        "company_name": stock.company_name,
        "price": stock.current_price,
        "change": change,
        "change_pct": percent(change, previous_close),
        "market": market.get_status(),
        "shares_owned": shares_owned,
    }


def change_text(quote):
    """Like "+$1.25 (+2.10%)"."""
    sign = ""
    if quote["change"] > 0:
        sign = "+"
    return f"{sign}{money(quote['change'])} ({sign}{quote['change_pct']}%)"


def remember_symbol(symbol):
    """Keep the last 5 symbols looked up, newest first, for "Search History" on the page."""
    history = session.get("symbol_history", [])
    if symbol in history:
        history.remove(symbol)
    history.insert(0, symbol)
    session["symbol_history"] = history[:5]


# --- Checking and placing an order -------------------------------------------------------------------

def check_order(user_id, order, lock_rows):
    """Every check an order must pass. Raises UserError if one fails.

    `order` is a dictionary with portfolio_id, symbol, action, unit and amount.
    With lock_rows=True, the rows are locked (SELECT ... FOR UPDATE) until we commit, so nobody
    else can change the cash, the shares or the price between our checks and our changes.
    Returns what place_order() needs: the portfolio, the stock, the holding, the number of
    shares, the price and the total.
    """
    portfolio_query = Portfolio.query.filter_by(portfolio_id=order["portfolio_id"], user_id=user_id)
    stock_query = Issuer.query.filter_by(symbol=order["symbol"])
    if lock_rows:
        portfolio_query = portfolio_query.with_for_update()
        stock_query = stock_query.with_for_update()

    portfolio = portfolio_query.first()
    if portfolio is None:
        abort(404)  # someone else's portfolio
    stock = stock_query.first()

    holding = None
    shares_owned = Decimal(0)
    if stock is not None:
        holding_query = Inventory.query.filter_by(portfolio_id=portfolio.portfolio_id,
                                                  issuer_id=stock.issuer_id)
        if lock_rows:
            holding_query = holding_query.with_for_update()
        holding = holding_query.first()
        if holding is not None:
            shares_owned = holding.quantity

    # Nobody can buy a delisted stock, but people who own it can still sell it.
    if stock is None or (not stock.is_listed and (order["action"] == "Buy" or shares_owned == 0)):
        raise UserError("Symbol not found")
    if not market.is_open():
        raise UserError("Market is closed")

    # How many shares, and what they cost. A $500 order buys as many shares as $500 pays for.
    price = stock.current_price
    if order["unit"] == "Dollars":
        quantity = round_shares_down(order["amount"] / price)
    else:
        quantity = order["amount"]
    total = to_cents(quantity * price)

    if order["action"] == "Buy":
        if quantity > stock.shares_available:
            raise UserError("Not enough shares available")
        if total > portfolio.cash_balance:
            raise UserError("Insufficient funds")
    else:
        if quantity > shares_owned:
            raise UserError("Quantity exceeds shares owned")

    return {
        "portfolio": portfolio,
        "stock": stock,
        "holding": holding,
        "shares_owned": shares_owned,
        "quantity": quantity,
        "price": price,
        "total": total,
    }


def buy(checked):
    """Make the changes for a buy: less cash, fewer shares left to buy, more shares owned."""
    portfolio = checked["portfolio"]
    stock = checked["stock"]
    holding = checked["holding"]
    quantity = checked["quantity"]
    price = checked["price"]

    portfolio.cash_balance = portfolio.cash_balance - checked["total"]
    stock.shares_available = stock.shares_available - quantity

    if holding is None:
        holding = Inventory(portfolio_id=portfolio.portfolio_id, issuer_id=stock.issuer_id,
                            quantity=quantity, average_cost=price)
        db.session.add(holding)
    else:
        # The new average price paid, over all the shares now owned.
        old_cost = holding.quantity * holding.average_cost
        new_cost = quantity * price
        new_quantity = holding.quantity + quantity
        holding.average_cost = to_cents((old_cost + new_cost) / new_quantity)
        holding.quantity = new_quantity


def sell(checked):
    """Make the changes for a sale: more cash, more shares left to buy, fewer shares owned."""
    portfolio = checked["portfolio"]
    stock = checked["stock"]
    holding = checked["holding"]
    quantity = checked["quantity"]

    portfolio.cash_balance = portfolio.cash_balance + checked["total"]
    stock.shares_available = stock.shares_available + quantity

    if quantity == holding.quantity:
        db.session.delete(holding)  # sold them all: remove the row instead of keeping 0 shares
    else:
        holding.quantity = holding.quantity - quantity  # the average price paid stays the same


def place_order(user_id, order):
    """Check the order again with the rows locked, then fill it.

    Everything is saved with one commit. If anything goes wrong, rollback() undoes all of it,
    so we never take the cash without adding the shares (or the other way round).
    """
    try:
        checked = check_order(user_id, order, lock_rows=True)
        if order["action"] == "Buy":
            buy(checked)
        else:
            sell(checked)

        now = now_for_database()
        trade = Transaction(portfolio_id=order["portfolio_id"], issuer_id=checked["stock"].issuer_id,
                            type=order["action"], quantity=checked["quantity"],
                            price_per_share=checked["price"], total_amount=checked["total"],
                            timestamp=now)
        db.session.add(trade)
        db.session.add(History(portfolio_id=order["portfolio_id"], trade=trade,
                               action=order["action"], timestamp=now))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {
        "transaction_id": trade.transaction_id,
        "symbol": checked["stock"].symbol,
        "action": order["action"],
        "quantity": checked["quantity"],
        "price": checked["price"],
        "total": checked["total"],
        "cash_balance": checked["portfolio"].cash_balance,
    }


# --- Pages -------------------------------------------------------------------------------------------

def show_trade_page(portfolio_id_text=None, symbol=None, form_values=None, errors=None):
    """The Trade page. When a symbol is given, it also shows the quote and the order form."""
    if form_values is None:
        form_values = {}
    if errors is None:
        errors = {}

    portfolios = list_portfolios(current_user.user_id)
    portfolio = choose_portfolio(current_user.user_id, portfolios, portfolio_id_text)

    quote = None
    if symbol and portfolio is not None:
        try:
            quote = get_quote(current_user.user_id, symbol, portfolio.portfolio_id)
            remember_symbol(quote["symbol"])
            symbol = quote["symbol"]
        except UserError as error:
            if "symbol" not in errors:
                errors["symbol"] = error.message

    market_text = None
    quote_change = None
    market_value = None
    can_sell = False
    if quote is not None:
        market_text = market.status_text(quote["market"])
        quote_change = change_text(quote)
        market_value = to_cents(quote["shares_owned"] * quote["price"])
        can_sell = quote["shares_owned"] > 0  # Sell is greyed out when they own none

    return render_template(
        "trade/trade.html",
        portfolios=portfolios,
        portfolio=portfolio,
        quote=quote,
        symbol=symbol or "",
        market_text=market_text,
        change_text=quote_change,
        market_value=market_value,
        can_sell=can_sell,
        history=session.get("symbol_history", []),
        values=form_values,
        errors=errors,
        actions=ACTIONS,
        units=UNITS,
        order_types=ORDER_TYPES,
        timings=TIMINGS,
    )


@bp.get("/trade")
@customer_required
def trade_page():
    symbol = request.args.get("symbol", "").strip()
    return show_trade_page(request.args.get("portfolio"), symbol)


@bp.post("/trade/preview")
@customer_required
def preview():
    """Check the order and show the "Review Your Order" page. Nothing is saved yet."""
    portfolio_id_text = request.form.get("portfolio", "")
    typed_symbol = request.form.get("symbol", "").strip()
    form_values = {
        "action": request.form.get("action", "").strip(),
        "unit": request.form.get("unit", "").strip(),
        "amount": request.form.get("amount", "").strip(),
    }
    if not portfolio_id_text.isdigit():
        abort(404)

    try:
        if form_values["action"] == "":
            raise UserError("Choose Buy or Sell.", "action")
        if form_values["unit"] == "Shares":
            amount = parse_shares(form_values["amount"])
        else:
            amount = parse_money(form_values["amount"])

        order = {
            "portfolio_id": int(portfolio_id_text),
            "symbol": typed_symbol.upper(),
            "action": form_values["action"],
            "unit": form_values["unit"],
            "amount": amount,
            "order_type": "Market",
            "timing": "Day Only",
        }
        checked = check_order(current_user.user_id, order, lock_rows=False)
    except UserError as error:
        errors = {error.field: error.message}
        return show_trade_page(portfolio_id_text, typed_symbol, form_values, errors)

    # Keep the order in the session until "Place order". The session can't hold a Decimal,
    # so the amount is saved as text.
    saved_order = dict(order)
    saved_order["amount"] = str(amount)
    session["pending_order"] = saved_order

    quote = get_quote(current_user.user_id, order["symbol"], order["portfolio_id"])
    review = {
        "request": order,
        "quote": quote,
        "quantity": checked["quantity"],
        "total": checked["total"],
    }
    return render_template("trade/confirm.html", order=review, portfolio=checked["portfolio"],
                           market_text=market.status_text(quote["market"]))


@bp.post("/trade/confirm")
@customer_required
def confirm():
    """Place the order that is waiting in the session."""
    # pop() takes the order out of the session, so pressing the button twice can't buy twice.
    order = session.pop("pending_order", None)
    if order is None:
        return show_trade_page(errors={"form": "This order has expired. Please review it again."})
    order["amount"] = Decimal(order["amount"])

    try:
        result = place_order(current_user.user_id, order)
    except UserError as error:
        form_values = {"action": order["action"], "unit": order["unit"], "amount": str(order["amount"])}
        return show_trade_page(str(order["portfolio_id"]), order["symbol"], form_values,
                               {error.field: error.message})

    portfolio = get_portfolio(current_user.user_id, order["portfolio_id"])
    filled_at = now_utc().astimezone(ARIZONA)
    return render_template("trade/done.html", result=result, portfolio=portfolio, executed_at=filled_at)
