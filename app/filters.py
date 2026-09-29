"""Jinja filters for showing values consistently on every page, and the stale-price flag."""
from __future__ import annotations

from decimal import Decimal

from flask import Flask

from app.money import to_cents


def money(value: Decimal | None) -> str:
    """$1,234.56 / -$1,234.56; an em dash when there is no value."""
    if value is None:
        return "—"
    cents = to_cents(value)
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents):,.2f}"


def shares(value: Decimal | None) -> str:
    """1,234.5 / 0.000237: up to 6 decimals, no trailing zeros; an em dash when there is no value."""
    if value is None:
        return "—"
    text = f"{value:,.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _price_status() -> dict:
    """`prices_stale` for base.html's "Prices may be delayed" banner (R7.8, A17). Signed-in pages only."""
    from flask import current_app
    from flask_login import current_user

    from app import clock
    from app.services.price_generator import prices_stale

    if not current_user.is_authenticated:
        return {"prices_stale": False}
    stale_seconds = current_app.config["SETTINGS"].stale_price_seconds
    try:
        return {"prices_stale": prices_stale(clock.now_utc(), stale_seconds)}
    except Exception:  # never let the banner break a page, least of all the 500 page after a database error
        current_app.logger.exception("could not check whether prices are stale")
        return {"prices_stale": False}


def init_filters(app: Flask) -> None:
    app.add_template_filter(money, "money")
    app.add_template_filter(shares, "shares")
    app.context_processor(_price_status)
