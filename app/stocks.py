"""The Stocks pages: a list of every stock (with a search box), and one stock's page with its
price chart.

Anyone who is logged in can open them. Only listed stocks appear; delisted ones are "not found".
"""
from datetime import timedelta

from flask import Blueprint, render_template, request
from sqlalchemy import or_

from app.common import percent, today
from app.models import Issuer, StockPrice

bp = Blueprint("stocks", __name__)

SEARCH_MAX_LENGTH = 100


def describe_stock(stock):
    """A stock as a dictionary, with its change since yesterday and its last 30 days of prices."""
    previous_close = stock.previous_close(today())
    change = stock.current_price - previous_close

    thirty_days_ago = today() - timedelta(days=30)
    recent_days = (
        StockPrice.query
        .filter(StockPrice.issuer_id == stock.issuer_id)
        .filter(StockPrice.price_date > thirty_days_ago)
        .order_by(StockPrice.price_date)
        .all()
    )
    sparkline = []  # the closing prices for the small chart
    for day in recent_days:
        sparkline.append(day.close_price)

    return {
        "symbol": stock.symbol,
        "company_name": stock.company_name,
        "exchange": stock.exchange,
        "description": stock.description,
        "price": stock.current_price,
        "previous_close": previous_close,
        "change": change,
        "change_pct": percent(change, previous_close),
        "sparkline": sparkline,
    }


def listed_stocks():
    """A query for every listed stock, in symbol order."""
    return Issuer.query.filter_by(is_listed=True).order_by(Issuer.symbol)


def chart_data(stocks, history=None):
    """The numbers charts.js needs to draw the charts. They go into the page as JSON."""
    sparklines = {}
    for stock in stocks:
        prices = []
        for price in stock["sparkline"]:
            prices.append(str(price))
        sparklines[stock["symbol"]] = prices
    data = {"sparks": sparklines}

    if history is not None:
        labels = []
        values = []
        for day in history:
            labels.append(day.price_date.isoformat())
            values.append(str(day.close_price))
        data["history"] = {"labels": labels, "values": values}
    return data


def show_stock_list(search, error=None, status_code=200):
    """The Stocks page: every listed stock, or only the ones matching `search`."""
    query = listed_stocks()
    if search and error is None:
        # In LIKE, % and _ mean "anything". We put | in front of them so they only match themselves.
        escaped = search.replace("|", "||").replace("%", "|%").replace("_", "|_")
        pattern = "%" + escaped + "%"  # contains the search text anywhere
        query = query.filter(or_(Issuer.symbol.like(pattern, escape="|"),
                                 Issuer.company_name.like(pattern, escape="|")))

    stocks = []
    for stock in query.all():
        stocks.append(describe_stock(stock))

    html = render_template("stocks/search.html", query=search, stocks=stocks, error=error,
                           search_max=SEARCH_MAX_LENGTH, chart=chart_data(stocks))
    return html, status_code


@bp.get("/stocks")
def search_page():
    search = request.args.get("q", "").strip()
    if len(search) > SEARCH_MAX_LENGTH:
        return show_stock_list(search, f"Search for {SEARCH_MAX_LENGTH} characters or fewer.")
    return show_stock_list(search)


@bp.get("/stocks/<symbol>")
def detail_page(symbol):
    stock = listed_stocks().filter_by(symbol=symbol.strip().upper()).first()
    if stock is None:
        return show_stock_list("", "Symbol not found", 404)

    one_year_ago = today() - timedelta(days=365)
    history = (
        StockPrice.query
        .filter(StockPrice.issuer_id == stock.issuer_id)
        .filter(StockPrice.price_date > one_year_ago)
        .order_by(StockPrice.price_date)
        .all()
    )

    # Today's opening price, if prices have moved today.
    open_price = None
    if len(history) > 0 and history[-1].price_date == today():
        open_price = history[-1].open_price

    described = describe_stock(stock)
    return render_template(
        "stocks/detail.html",
        stock=described,
        detail={"exchange": stock.exchange, "description": stock.description, "open_price": open_price},
        search_max=SEARCH_MAX_LENGTH,
        chart=chart_data([described], history),
    )
