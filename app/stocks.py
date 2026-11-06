"""The Stocks pages: a list of every stock (with a search box), and one stock's page with its
price chart.

Anyone who is logged in can open them. Only listed stocks appear; delisted ones are "not found".
"""
from datetime import timedelta

from flask import Blueprint, render_template, request
from sqlalchemy import or_

from app.common import today
from app.models import Issuer, StockPrice

bp = Blueprint("stocks", __name__)

SEARCH_MAX_LENGTH = 100


def listed_stocks():
    """A query for every listed stock, in symbol order."""
    return Issuer.query.filter_by(is_listed=True).order_by(Issuer.symbol)


def chart_data(stocks, history=None):
    """The numbers charts.js needs to draw the charts. They go into the page as JSON.

    sparks    each stock's last 30 closing prices, for the small charts
    history   one stock's daily closing prices for a year, for its big chart
    """
    sparklines = {}
    for stock in stocks:
        sparklines[stock.symbol] = [str(price) for price in stock.recent_closes(30)]
    data = {"sparks": sparklines}

    if history is not None:
        data["history"] = {
            "labels": [day.price_date.isoformat() for day in history],
            "values": [str(day.close_price) for day in history],
        }
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
    stocks = query.all()

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

    return render_template("stocks/detail.html", stock=stock, open_price=open_price,
                           search_max=SEARCH_MAX_LENGTH, chart=chart_data([stock], history))
