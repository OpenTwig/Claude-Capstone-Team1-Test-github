"""Stocks: adding, editing, delisting, searching, and looking up (design: Price generator
"Randomize History", Valuation and market data "Search"; R7.1-R7.2, R7.9, R14, R16.7, A9, A15, D16, D20).
"""
from __future__ import annotations

import random
import re
from datetime import date, timedelta
from decimal import Decimal

import pymysql
from flask import current_app

from app import clock
from app.db import get_db, transaction
from app.errors import DuplicateSymbol, NotFound, SymbolNotFound, ValidationFailed
from app.money import CENT, MAX_MONEY, MAX_SHARES, SHARE, TOO_MANY_SHARES, TOO_MUCH_MONEY, to_cents
from app.services import validation
from app.services.market import is_trading_day, market_tz
from app.services.pricing import FLOOR, PriceModel, history_walk
from app.services.types import StockRow
from app.services.users import require_admin

DUPLICATE_KEY = 1062
SYMBOL = re.compile(r"[A-Z0-9]{1,10}")
DESCRIPTION_MAX = 50  # A15: the wireframe's limit (the column allows 500)
SEARCH_MAX = 100
HISTORY_CALENDAR_DAYS = 365  # A9: one year of past trading days

_COLUMNS = "issuerID, ticker_symbol, company_name, exchange, current_price, description, shares_available, is_listed"


def _row(r: dict) -> StockRow:
    return StockRow(
        issuer_id=r["issuerID"],
        symbol=r["ticker_symbol"],
        company_name=r["company_name"],
        exchange=r["exchange"],
        current_price=r["current_price"],
        description=r["description"],
        shares_available=r["shares_available"],
        is_listed=bool(r["is_listed"]),
    )


def normalize_symbol(symbol: str | None, field: str = "symbol") -> str:
    """Upper-case and check a ticker: 1-10 letters A-Z and digits (R14.2)."""
    cleaned = (symbol or "").strip().upper()
    if not SYMBOL.fullmatch(cleaned):
        raise ValidationFailed(field, "Symbol must be 1 to 10 letters (A-Z) or digits.")
    return cleaned


def _validated_price(price: Decimal) -> Decimal:
    if isinstance(price, Decimal) and price.is_finite() and price > MAX_MONEY:
        raise ValidationFailed("price", TOO_MUCH_MONEY)
    if not isinstance(price, Decimal) or not price.is_finite() or price < CENT or price > MAX_MONEY or to_cents(price) != price:
        raise ValidationFailed("price", "Enter a price greater than $0.00 with at most 2 decimal places.")
    return price


def _validated_shares(shares: Decimal) -> Decimal:
    """Number of shares available: more than 0, at most 6 decimal places (AR03, D16)."""
    if isinstance(shares, Decimal) and shares.is_finite() and shares > MAX_SHARES:
        raise ValidationFailed("shares", TOO_MANY_SHARES)
    if (
        not isinstance(shares, Decimal)
        or not shares.is_finite()
        or shares <= 0
        or shares > MAX_SHARES
        or shares.quantize(SHARE) != shares
    ):
        raise ValidationFailed("shares", "Enter a number of shares greater than 0 (at most 6 decimal places).")
    return shares


def _text_fields(company_name, description, exchange) -> tuple[str, str | None, str | None]:
    return (
        validation.text("company_name", company_name, "Stock name", 255),
        validation.text("description", description, "Description", DESCRIPTION_MAX, required=False),
        validation.text("exchange", exchange, "Exchange", 10, required=False),
    )


def past_trading_days(today: date, calendar_days: int = HISTORY_CALENDAR_DAYS) -> list[date]:
    """Trading days in the `calendar_days` before `today`, oldest first (weekends and NYSE holidays skipped)."""
    days = (today - timedelta(days=n) for n in range(calendar_days, 0, -1))
    return [d for d in days if is_trading_day(d)]


def price_rows(issuer_id: int, price: Decimal, today: date, randomize: bool, rng: random.Random, model: PriceModel):
    """(issuerID, date, open, close) rows for a new stock. Today's row is open = close = price (A9)."""
    rows = []
    if randomize:
        days = past_trading_days(today)
        closes = history_walk(price, len(days), rng, model)
        for k, day in enumerate(days, start=1):
            rows.append((issuer_id, day, max(FLOOR, to_cents(closes[k - 1])), max(FLOOR, to_cents(closes[k]))))
    rows.append((issuer_id, today, price, price))
    return rows


def add_stock(
    admin_user_id: int,
    company_name: str,
    symbol: str,
    price: Decimal,
    description: str | None,
    exchange: str | None,
    randomize_history: bool,
    shares: Decimal,
    rng: random.Random | None = None,
) -> int:
    """Create a tradable stock at `price` with `shares` available (R14.2-R14.6, SR01, AR03, D16).

    Returns the issuerID.
    """
    require_admin(admin_user_id)
    company_name, description, exchange = _text_fields(company_name, description, exchange)
    symbol = normalize_symbol(symbol)
    price = _validated_price(price)
    shares = _validated_shares(shares)

    settings = current_app.config["SETTINGS"]
    model = PriceModel.from_settings(settings)
    today = clock.now_local(market_tz()).date()
    try:
        with transaction() as cur:
            cur.execute(
                "INSERT INTO Issuer (company_name, ticker_symbol, exchange, current_price, description, shares_available)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (company_name, symbol, exchange, price, description, shares),
            )
            issuer_id = cur.lastrowid
            cur.executemany(
                "INSERT INTO StockPrice (issuerID, price_date, open_price, close_price) VALUES (%s, %s, %s, %s)",
                price_rows(issuer_id, price, today, randomize_history, rng or random.Random(), model),
            )
    except pymysql.err.IntegrityError as exc:
        if exc.args[0] == DUPLICATE_KEY:
            raise DuplicateSymbol() from exc  # R14.4
        raise
    return issuer_id


def _get_by_id(issuer_id: int) -> StockRow:
    with get_db().cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM Issuer WHERE issuerID = %s", (issuer_id,))
        found = cur.fetchone()
    get_db().commit()
    if found is None:
        raise NotFound()
    return _row(found)


def update_stock(
    admin_user_id: int, issuer_id: int, company_name: str, description: str | None, exchange: str | None
) -> StockRow:
    """Edit a stock's name, description, and exchange (R14.8, D20).

    The symbol and price can't be edited: prices come only from the price generator.
    """
    require_admin(admin_user_id)
    company_name, description, exchange = _text_fields(company_name, description, exchange)
    with transaction() as cur:
        cur.execute(
            "UPDATE Issuer SET company_name = %s, description = %s, exchange = %s WHERE issuerID = %s",
            (company_name, description, exchange, issuer_id),
        )
    return _get_by_id(issuer_id)


def delist_stock(admin_user_id: int, issuer_id: int) -> None:
    """Take a stock off the market (R14.9, D20): no longer searchable or buyable.

    Holdings are untouched, so customers who own it can still sell (R9.10).
    """
    require_admin(admin_user_id)
    _get_by_id(issuer_id)  # NotFound for an unknown stock
    with transaction() as cur:
        cur.execute("UPDATE Issuer SET is_listed = FALSE WHERE issuerID = %s", (issuer_id,))


def search_stocks(query: str | None) -> list[StockRow]:
    """Listed stocks whose symbol or company name contains `query`, case-insensitively (R7.1, R7.9, P15).

    LIKE wildcards in the query (% and _) are escaped, so they match literally.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return []
    if len(cleaned) > SEARCH_MAX:
        raise ValidationFailed("q", f"Search for {SEARCH_MAX} characters or fewer.")
    pattern = "%" + cleaned.replace("|", "||").replace("%", "|%").replace("_", "|_") + "%"
    with get_db().cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM Issuer"
            " WHERE is_listed AND (ticker_symbol LIKE %s ESCAPE '|' OR company_name LIKE %s ESCAPE '|')"
            " ORDER BY ticker_symbol",
            (pattern, pattern),
        )
        rows = cur.fetchall()
    get_db().commit()
    return [_row(r) for r in rows]


def get_stock(symbol: str | None, include_delisted: bool = False) -> StockRow:
    """The stock with this symbol, or SymbolNotFound (R8.2).

    Delisted stocks count as not found (D20) unless include_delisted is set, which the
    sell side uses so customers can still sell a delisted stock they hold (R9.10).
    """
    try:
        normalized = normalize_symbol(symbol)
    except ValidationFailed as exc:
        raise SymbolNotFound() from exc
    with get_db().cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM Issuer WHERE ticker_symbol = %s", (normalized,))
        found = cur.fetchone()
    get_db().commit()
    if found is None or (not found["is_listed"] and not include_delisted):
        raise SymbolNotFound()
    return _row(found)