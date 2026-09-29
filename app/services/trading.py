"""Buying and selling stock (design: Trading; R8, R9, A3, A4, D11, D16, D19, D20;
properties P3, P5, P7, P8, P9, P18).

preview_order() checks an order against current prices without locking anything,
for the confirmation screen. execute_order() runs every check again inside one
database transaction, under row locks taken in the order Portfolio -> Inventory
-> Issuer, and executes at the price at that moment (D11). Any failure rolls the
whole trade back.
"""
from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app import clock
from app.db import get_db, retry_once_on_lock_conflict, transaction
from app.errors import (
    InsufficientFunds,
    InsufficientShares,
    MarketClosed,
    NotEnoughSharesAvailable,
    NotFound,
    SymbolNotFound,
    ValidationFailed,
)
from app.money import (
    CENT,
    MAX_MONEY,
    MAX_MONEY_TEXT,
    MAX_SHARES,
    SHARE,
    TOO_MANY_SHARES,
    TOO_MUCH_MONEY,
    to_cents,
    to_shares_floor,
)
from app.services import market, portfolios, pricing, stocks
from app.services.types import OrderPreview, OrderRequest, OrderResult, Quote, StockRow
from app.services.users import require_customer

ACTIONS = ("Buy", "Sell")
UNITS = ("Dollars", "Shares")  # A3
ORDER_TYPES = ("Market",)      # A4
TIMINGS = ("Day Only",)        # A4
ZERO = Decimal("0")


def _validated(req: OrderRequest) -> OrderRequest:
    if req.action not in ACTIONS:
        raise ValidationFailed("action", "Choose Buy or Sell.")
    if req.unit not in UNITS:
        raise ValidationFailed("unit", "Choose Dollars or Shares.")
    if req.order_type not in ORDER_TYPES:
        raise ValidationFailed("order_type", "Only Market orders are available.")
    if req.timing not in TIMINGS:
        raise ValidationFailed("timing", "Only Day Only orders are available.")
    amount = req.amount
    valid = isinstance(amount, Decimal) and amount.is_finite() and amount > 0
    if valid and req.unit == "Dollars" and amount > MAX_MONEY:
        raise ValidationFailed("amount", TOO_MUCH_MONEY)
    if valid and req.unit == "Shares" and amount > MAX_SHARES:
        raise ValidationFailed("amount", TOO_MANY_SHARES)
    if req.unit == "Dollars":
        if not valid or amount > MAX_MONEY or to_cents(amount) != amount:
            raise ValidationFailed("amount", "Enter a dollar amount greater than $0.00 with at most 2 decimal places.")
    elif not valid or amount > MAX_SHARES or amount.quantize(SHARE) != amount:
        raise ValidationFailed("amount", "Enter a number of shares greater than 0 with at most 6 decimal places.")
    return req


def size_order(req: OrderRequest, price: Decimal) -> tuple[Decimal, Decimal]:
    """Quantity and total at this price (A3). A Dollars order never costs more than the amount."""
    quantity = to_shares_floor(req.amount / price) if req.unit == "Dollars" else req.amount
    if quantity <= 0:
        raise ValidationFailed("amount", "That amount buys less than 0.000001 of a share at the current price.")
    total = to_cents(quantity * price)
    if total < CENT:
        raise ValidationFailed("amount", "The order total must be at least $0.01.")
    if total > MAX_MONEY:
        raise ValidationFailed("amount", f"An order total can't be more than {MAX_MONEY_TEXT}.")
    return quantity, total


def _shares_owned(portfolio_id: int, issuer_id: int) -> Decimal:
    with get_db().cursor() as cur:
        cur.execute("SELECT quantity FROM Inventory WHERE portfolioID = %s AND issuerID = %s", (portfolio_id, issuer_id))
        found = cur.fetchone()
    get_db().commit()
    return found["quantity"] if found else ZERO


def _tradable_stock(user_id: int, symbol: str, portfolio_id: int | None) -> tuple[StockRow, Decimal | None]:
    """The stock and the shares held in the portfolio (if one is given).

    A delisted stock counts as not found, except to a customer whose portfolio holds it (D20, R9.10).
    """
    stock = stocks.get_stock(symbol, include_delisted=True)
    owned = None
    if portfolio_id is not None:
        portfolios.get_owned_portfolio(user_id, portfolio_id)  # NotFound unless it is theirs (R2.5)
        owned = _shares_owned(portfolio_id, stock.issuer_id)
    if not stock.is_listed and not owned:
        raise SymbolNotFound()
    return stock, owned


def quote(user_id: int, symbol: str, portfolio_id: int | None = None, at_utc: datetime | None = None) -> Quote:
    """Name, price, change since the previous close, market status, and shares owned (R8.2, R9.1)."""
    now_utc = at_utc or clock.now_utc()
    stock, owned = _tradable_stock(user_id, symbol, portfolio_id)
    today = now_utc.astimezone(market.market_tz()).date()
    previous = pricing.previous_close(stock.issuer_id, today)
    change = stock.current_price - previous
    return Quote(
        symbol=stock.symbol,
        company_name=stock.company_name,
        price=stock.current_price,
        previous_close=previous,
        change=change,
        change_pct=(change / previous * 100).quantize(CENT, rounding=ROUND_HALF_UP),
        market=market.current_status(now_utc),
        shares_owned=owned,
    )


def preview_order(user_id: int, req: OrderRequest, at_utc: datetime | None = None) -> OrderPreview:
    """Check an order at the current price, for the confirmation screen (R8.3-R8.6, R9.2-R9.5). Changes nothing."""
    require_customer(user_id)  # D19
    req = _validated(req)
    now_utc = at_utc or clock.now_utc()
    stock, owned = _tradable_stock(user_id, req.symbol, req.portfolio_id)
    if req.action == "Buy" and not stock.is_listed:
        raise SymbolNotFound()  # D20
    if not market.is_market_open(now_utc):
        raise MarketClosed()
    quantity, total = size_order(req, stock.current_price)
    if req.action == "Buy":
        if quantity > stock.shares_available:
            raise NotEnoughSharesAvailable()
        if total > portfolios.get_owned_portfolio(user_id, req.portfolio_id).cash_balance:
            raise InsufficientFunds()
    elif quantity > owned:
        raise InsufficientShares()
    return OrderPreview(req, quote(user_id, req.symbol, req.portfolio_id, now_utc), quantity, total)


def _record_trade(cur, *, portfolio_id: int, issuer_id: int, action: str, quantity: Decimal, price: Decimal,
                  total: Decimal, now: datetime) -> int:
    cur.execute(
        "INSERT INTO `Transaction` (portfolioID, issuerID, type, quantity, price_per_share, total_amount, status,"
        " `timestamp`, order_type, timing) VALUES (%s, %s, %s, %s, %s, %s, 'Filled', %s, 'Market', 'Day Only')",
        (portfolio_id, issuer_id, action, quantity, price, total, now),
    )
    transaction_id = cur.lastrowid
    cur.execute(
        "INSERT INTO History (portfolioID, transactionID, action, `timestamp`) VALUES (%s, %s, %s, %s)",
        (portfolio_id, transaction_id, action, now),
    )
    return transaction_id


@retry_once_on_lock_conflict
def execute_order(user_id: int, req: OrderRequest, at_utc: datetime | None = None) -> OrderResult:
    """Execute a confirmed order at the current price (R8.7, R9.6, D11). All of it, or none of it (R8.10, R9.9)."""
    require_customer(user_id)  # D19
    req = _validated(req)
    now_utc = at_utc or clock.now_utc()
    now = now_utc.replace(tzinfo=None)
    issuer_id = stocks.get_stock(req.symbol, include_delisted=True).issuer_id
    with transaction(isolation="READ COMMITTED") as cur:
        cur.execute(
            "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s AND userID = %s FOR UPDATE",
            (req.portfolio_id, user_id),
        )
        portfolio = cur.fetchone()
        if portfolio is None:
            raise NotFound()  # someone else's portfolio (R2.5)
        cur.execute(
            "SELECT quantity, average_cost FROM Inventory WHERE portfolioID = %s AND issuerID = %s FOR UPDATE",
            (req.portfolio_id, issuer_id),
        )
        position = cur.fetchone()
        cur.execute(
            "SELECT current_price, shares_available, is_listed FROM Issuer WHERE issuerID = %s FOR UPDATE",
            (issuer_id,),
        )
        issuer = cur.fetchone()
        owned = position["quantity"] if position else ZERO
        if not issuer["is_listed"] and (req.action == "Buy" or not owned):
            raise SymbolNotFound()  # D20
        if not market.is_open(now_utc, market.current_schedule(cur), market.market_tz()):
            raise MarketClosed()  # R8.4, R9.3

        price = issuer["current_price"]
        quantity, total = size_order(req, price)
        cash = portfolio["cash_balance"]
        if req.action == "Buy":
            if quantity > issuer["shares_available"]:
                raise NotEnoughSharesAvailable()  # R8.11
            if total > cash:
                raise InsufficientFunds()  # R8.5
            new_cash, new_supply = cash - total, issuer["shares_available"] - quantity
            if position:
                held, average = position["quantity"], position["average_cost"]
                new_quantity = held + quantity
                new_average = to_cents((held * average + quantity * price) / new_quantity)
                cur.execute(
                    "UPDATE Inventory SET quantity = %s, average_cost = %s WHERE portfolioID = %s AND issuerID = %s",
                    (new_quantity, new_average, req.portfolio_id, issuer_id),
                )
            else:
                cur.execute(
                    "INSERT INTO Inventory (portfolioID, issuerID, quantity, average_cost) VALUES (%s, %s, %s, %s)",
                    (req.portfolio_id, issuer_id, quantity, price),
                )
        else:
            if quantity > owned:
                raise InsufficientShares()  # R9.4
            new_cash, new_supply = cash + total, issuer["shares_available"] + quantity  # CR08
            if new_cash > MAX_MONEY:
                raise ValidationFailed("amount", f"That sale would take the cash balance over the maximum of {MAX_MONEY_TEXT}.")
            if quantity == owned:
                cur.execute("DELETE FROM Inventory WHERE portfolioID = %s AND issuerID = %s",
                            (req.portfolio_id, issuer_id))  # R9.6: no zero positions
            else:
                cur.execute(
                    "UPDATE Inventory SET quantity = %s WHERE portfolioID = %s AND issuerID = %s",
                    (owned - quantity, req.portfolio_id, issuer_id),  # average cost unchanged (R9.6)
                )
        cur.execute("UPDATE Portfolio SET cash_balance = %s WHERE portfolioID = %s", (new_cash, req.portfolio_id))
        cur.execute("UPDATE Issuer SET shares_available = %s WHERE issuerID = %s", (new_supply, issuer_id))
        transaction_id = _record_trade(cur, portfolio_id=req.portfolio_id, issuer_id=issuer_id, action=req.action,
                                       quantity=quantity, price=price, total=total, now=now)
    return OrderResult(transaction_id, stocks.normalize_symbol(req.symbol), req.action, quantity, price, total,
                       new_cash)
