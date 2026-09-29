"""Trade page (wf-2): quote lookup, the trade ticket, a confirmation screen, and the order
confirmation (R8, R9; design: Trading, DD10, D11).

The order is checked twice: /trade/preview shows the confirmation without changing anything,
and /trade/confirm executes it. Between the two steps the order lives in the (signed) session
with a one-time nonce and a 2-minute expiry, so the confirm step reads nothing but the nonce
from the form: price, total, quantity, or portfolio fields posted to it are ignored.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, Flask, jsonify, render_template, request, session
from flask_login import current_user

from app import clock
from app.auth import customer_required
from app.errors import DomainError, NotFound, OrderExpired, SymbolNotFound, ValidationFailed
from app.filters import money, shares
from app.money import parse_money, parse_shares, to_cents
from app.services import portfolios, trading
from app.services.market import market_tz
from app.services.trading import ACTIONS, ORDER_TYPES, TIMINGS, UNITS
from app.services.types import MarketStatus, OrderRequest, Quote

bp = Blueprint("trade", __name__)

PENDING_KEY = "pending_order"
HISTORY_KEY = "symbol_history"
HISTORY_SIZE = 5                      # A13
ORDER_LIFETIME = timedelta(minutes=2)  # design: Trading (nonce, 2-min expiry)
TICKET_FIELDS = ("action", "unit", "amount", "order_type", "timing")


# --- Helpers ------------------------------------------------------------------------------------------

def market_text(status: MarketStatus) -> str:
    """"Market open · closes 5:00 PM" / "Market closed · opens Mon 9:00 AM" (R8.2)."""
    if status.is_open:
        return f"Market open · closes {status.closes_at.strftime('%I:%M %p').lstrip('0')}"
    if status.next_open is None:
        return "Market closed"
    return f"Market closed · opens {status.next_open.strftime('%a')} {status.next_open.strftime('%I:%M %p').lstrip('0')}"


def change_text(q: Quote) -> str:
    sign = "+" if q.change > 0 else ""
    return f"{sign}{money(q.change)} ({sign}{q.change_pct}%)"


def market_value(q: Quote) -> Decimal:
    """What the shares owned are worth at the current price, to the cent (R9.1)."""
    return to_cents((q.shares_owned or Decimal(0)) * q.price)


def _remember_symbol(symbol: str) -> list[str]:
    """Keep the customer's last 5 looked-up symbols, newest first, in the session only (A13)."""
    history = [s for s in session.get(HISTORY_KEY, []) if s != symbol]
    session[HISTORY_KEY] = [symbol, *history][:HISTORY_SIZE]
    return session[HISTORY_KEY]


def _selected_portfolio(owned, raw: str | None):
    """The portfolio chosen in the URL or form, or the first one. Someone else's portfolio is a 404 (R2.5)."""
    if not owned:
        return None
    if raw in (None, ""):
        return owned[0]
    if not str(raw).isdigit():
        raise NotFound()
    return portfolios.get_owned_portfolio(current_user.user_id, int(raw))


def _parse_amount(unit: str, text: str):
    """Dollars or shares, per Unit (A3). Raises ValidationFailed on the amount field."""
    try:
        return parse_shares(text) if unit == "Shares" else parse_money(text)
    except ValueError as exc:
        raise ValidationFailed("amount", str(exc)) from exc


def _page(*, portfolio_raw: str | None = None, symbol: str | None = None, values: dict | None = None,
          errors: dict | None = None, status: int = 200):
    """The Trade page. With a symbol, it shows the quote and the full ticket (wf-2, second state)."""
    user_id = current_user.user_id
    owned = portfolios.list_portfolios(user_id)
    portfolio = _selected_portfolio(owned, portfolio_raw)
    errors = dict(errors or {})
    quote = None
    if symbol and portfolio is not None:
        try:
            quote = trading.quote(user_id, symbol, portfolio.portfolio_id)  # R8.2, R9.1
            _remember_symbol(quote.symbol)
        except SymbolNotFound as exc:
            errors.setdefault("symbol", exc.message)
    return render_template(
        "trade/trade.html",
        portfolios=owned, portfolio=portfolio, quote=quote,
        symbol=(quote.symbol if quote else (symbol or "")),
        market_text=market_text(quote.market) if quote else None,
        change_text=change_text(quote) if quote else None,
        market_value=market_value(quote) if quote else None,
        can_sell=bool(quote and quote.shares_owned),  # Sell is greyed out with no shares (user feedback)
        history=session.get(HISTORY_KEY, []),
        values=values or {}, errors=errors,
        actions=ACTIONS, units=UNITS, order_types=ORDER_TYPES, timings=TIMINGS,
    ), status


def _request_from(form) -> tuple[OrderRequest, dict]:
    values = {k: (form.get(k) or "").strip() for k in ("portfolio", "symbol", *TICKET_FIELDS)}
    if not values["portfolio"].isdigit():
        raise NotFound()
    unit = values["unit"] or "Dollars"
    req = OrderRequest(
        portfolio_id=int(values["portfolio"]), symbol=values["symbol"], action=values["action"], unit=unit,
        amount=_parse_amount(unit, values["amount"]),
        order_type=values["order_type"] or ORDER_TYPES[0], timing=values["timing"] or TIMINGS[0],
    )
    return req, values


# --- Routes -------------------------------------------------------------------------------------------

@bp.get("/trade")
@customer_required
def trade_page():
    return _page(portfolio_raw=request.args.get("portfolio"), symbol=request.args.get("symbol", "").strip() or None)


@bp.get("/api/quote/<symbol>")
@customer_required
def quote_api(symbol: str):
    """Quote JSON for the ticket (R8.2, R9.1). Someone else's portfolio is a 404, like the page (R2.5)."""
    raw = request.args.get("portfolio", "")
    if raw and not raw.isdigit():
        return jsonify(error="Not found"), 404
    try:
        q = trading.quote(current_user.user_id, symbol, int(raw) if raw else None)
    except SymbolNotFound as exc:
        return jsonify(error=exc.message), 404
    except NotFound:
        return jsonify(error="Not found"), 404
    history = _remember_symbol(q.symbol)
    return jsonify(
        symbol=q.symbol, company_name=q.company_name,
        price=str(q.price), price_text=money(q.price),
        previous_close=str(q.previous_close), change=str(q.change), change_pct=str(q.change_pct),
        change_text=change_text(q), change_direction=(q.change > 0) - (q.change < 0),
        market_open=q.market.is_open, market_text=market_text(q.market),
        shares_owned=None if q.shares_owned is None else str(q.shares_owned),
        shares_owned_text=shares(q.shares_owned or Decimal(0)), market_value_text=money(market_value(q)),
        history=history,
    )


@bp.post("/trade/preview")
@customer_required
def preview():
    form = request.form
    portfolio_raw, symbol = form.get("portfolio"), (form.get("symbol") or "").strip()
    values = {k: form.get(k, "") for k in TICKET_FIELDS}
    try:
        req, _ = _request_from(form)
        order = trading.preview_order(current_user.user_id, req)  # R8.3-R8.6, R9.2-R9.5
    except NotFound:
        raise  # someone else's portfolio (R2.5)
    except ValidationFailed as exc:
        return _page(portfolio_raw=portfolio_raw, symbol=symbol, values=values, errors={exc.field: exc.message})
    except DomainError as exc:  # Market is closed, Insufficient funds, Quantity exceeds shares owned, ...
        return _page(portfolio_raw=portfolio_raw, symbol=symbol, values=values, errors={"form": exc.message})
    nonce = secrets.token_urlsafe(16)
    session[PENDING_KEY] = {
        "nonce": nonce,
        "expires": (clock.now_utc() + ORDER_LIFETIME).isoformat(),
        "portfolio_id": req.portfolio_id, "symbol": order.quote.symbol, "action": req.action, "unit": req.unit,
        "amount": str(req.amount), "order_type": req.order_type, "timing": req.timing,
    }
    portfolio = portfolios.get_owned_portfolio(current_user.user_id, req.portfolio_id)
    return render_template("trade/confirm.html", order=order, portfolio=portfolio, nonce=nonce,
                           market_text=market_text(order.quote.market))


@bp.post("/trade/confirm")
@customer_required
def confirm():
    pending = session.pop(PENDING_KEY, None)  # used once: a second submit finds nothing (design: Trading)
    nonce = request.form.get("nonce", "")
    if (pending is None or not secrets.compare_digest(pending["nonce"], nonce)
            or clock.now_utc() > datetime.fromisoformat(pending["expires"])):
        portfolio = str(pending["portfolio_id"]) if pending else None
        return _page(portfolio_raw=portfolio, symbol=pending["symbol"] if pending else None,
                     errors={"form": OrderExpired.message})
    req = OrderRequest(pending["portfolio_id"], pending["symbol"], pending["action"], pending["unit"],
                       Decimal(pending["amount"]), pending["order_type"], pending["timing"])
    values = {"action": req.action, "unit": req.unit, "amount": pending["amount"]}
    try:
        result = trading.execute_order(current_user.user_id, req)  # R8.7, R9.6 (checks run again)
    except NotFound:
        raise
    except ValidationFailed as exc:
        return _page(portfolio_raw=str(req.portfolio_id), symbol=req.symbol, values=values,
                     errors={exc.field: exc.message})
    except DomainError as exc:
        return _page(portfolio_raw=str(req.portfolio_id), symbol=req.symbol, values=values,
                     errors={"form": exc.message})
    portfolio = portfolios.get_owned_portfolio(current_user.user_id, req.portfolio_id)
    executed_at = clock.now_utc().astimezone(market_tz())
    return render_template("trade/done.html", result=result, portfolio=portfolio, executed_at=executed_at)


def init_trade(app: Flask) -> None:
    app.register_blueprint(bp)
