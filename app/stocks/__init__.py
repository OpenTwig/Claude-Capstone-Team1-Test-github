"""Stocks pages (wf-1): search with Top Gainers and Trending, and the stock detail view (R7).

Open to every signed-in user, customers and administrators alike (R2, D19).
"""
from __future__ import annotations

from flask import Blueprint, Flask, render_template, request

from app.errors import SymbolNotFound, ValidationFailed
from app.services import valuation
from app.services.stocks import SEARCH_MAX

bp = Blueprint("stocks", __name__)


def _chart_data(movers, detail=None) -> dict:
    """Numbers for stocks.js, rendered as a JSON data block (not a script), so the CSP needs no exceptions."""
    data = {"sparks": {m.symbol: [str(p) for p in m.sparkline] for m in movers}}
    if detail is not None:
        data["history"] = {"labels": [d.isoformat() for d, _ in detail.history],
                           "values": [str(p) for _, p in detail.history]}
    return data


def _search_page(query: str, results, error, status: int = 200):
    gainers, trending = valuation.top_gainers(), valuation.trending()
    return render_template("stocks/search.html", query=query, results=results, error=error,
                           gainers=gainers, trending=trending, search_max=SEARCH_MAX,
                           chart=_chart_data([*(results or []), *gainers, *trending])), status


@bp.get("/stocks")
def search_page():
    query = request.args.get("q", "").strip()
    results, error = None, None
    if query:
        try:
            results = valuation.search(query)  # R7.1
        except ValidationFailed as exc:
            error = exc.message
    return _search_page(query, results, error)


@bp.get("/stocks/<symbol>")
def detail_page(symbol: str):
    try:
        detail = valuation.stock_detail(symbol)  # R7.4
    except SymbolNotFound as exc:  # unknown or delisted (D20)
        return _search_page("", None, exc.message, 404)
    gainers, trending = valuation.top_gainers(), valuation.trending()
    return render_template("stocks/detail.html", detail=detail, stock=detail.mover,
                           gainers=gainers, trending=trending, search_max=SEARCH_MAX,
                           chart=_chart_data([*gainers, *trending], detail))


def init_stocks(app: Flask) -> None:
    app.register_blueprint(bp)
