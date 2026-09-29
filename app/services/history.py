"""Transaction history (design: History; R13, R17, R19.4, A12, P16).

Every trade and transfer has a History row (R17.1). This module lists a portfolio's rows for an
Arizona date range, newest first, with a description, and optionally only one symbol's trades.
Customers see only their own portfolios (R13.7); administrators may choose any (R13.6).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from app import clock
from app.crypto import decrypt_field, mask_last4
from app.db import get_db
from app.errors import NotFound
from app.filters import money, shares
from app.services import portfolios, stocks, users
from app.services.market import market_tz
from app.services.paging import ListPage, clamp
from app.services.types import HistoryRow

# A12: the Date Range choices, in menu order; Last 6 Months is the default.
RANGES: dict[str, str] = {
    "30d": "Last 30 Days",
    "3m": "Last 3 Months",
    "6m": "Last 6 Months",
    "12m": "Last 12 Months",
    "ytd": "Year to Date",
}
DEFAULT_RANGE = "6m"
PER_PAGE = 20  # rows per History page


@dataclass(frozen=True)
class PortfolioChoice:
    portfolio_id: int
    label: str  # "Growth •••• 6281", or "Casey Customer: Growth •••• 6281" for administrators (R13.6)


def _months_back(day: date, months: int) -> date:
    """The same day of the month `months` earlier, or that month's last day if it is shorter."""
    year, month_zero = divmod(day.year * 12 + day.month - 1 - months, 12)
    month = month_zero + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def date_range(key: str | None, today: date) -> tuple[date, date]:
    """The (start, end) Arizona dates for a Date Range choice, both inclusive (A12). Unknown keys use the default."""
    key = key if key in RANGES else DEFAULT_RANGE
    if key == "30d":
        return today - timedelta(days=30), today
    if key == "ytd":
        return date(today.year, 1, 1), today
    return _months_back(today, {"3m": 3, "6m": 6, "12m": 12}[key]), today


def _utc_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Arizona midnight at the start, and Arizona midnight after the end, as naive UTC (timestamps are stored in UTC)."""
    tz = market_tz()
    def to_utc(day):
        return datetime.combine(day, time.min, tz).astimezone(timezone.utc).replace(tzinfo=None)
    return to_utc(start), to_utc(end + timedelta(days=1))


def _is_admin(viewer_id: int) -> bool:
    viewer = users.get_user(viewer_id)
    return bool(viewer and viewer.is_admin)


def choices(viewer_id: int) -> list[PortfolioChoice]:
    """The portfolio selector (R13.1): the customer's own, or every customer's for an administrator (R13.6)."""
    if not _is_admin(viewer_id):
        return [PortfolioChoice(p.portfolio_id, f"{p.account_name} {p.masked_account_number}")
                for p in portfolios.list_portfolios(viewer_id)]
    with get_db().cursor() as cur:
        cur.execute(
            "SELECT p.portfolioID, p.account_name, p.account_number, u.name FROM Portfolio p"
            " JOIN `User` u ON u.userID = p.userID ORDER BY u.name, p.account_name, p.portfolioID"
        )
        rows = cur.fetchall()
    get_db().commit()
    return [PortfolioChoice(r["portfolioID"], f"{r['name']}: {r['account_name']} {mask_last4(r['account_number'])}")
            for r in rows]


ALL = None  # search_history(portfolio_id=ALL): every customer's history, administrators only


def _check_access(viewer_id: int, portfolio_id: int | None) -> None:
    """Customers: their own portfolios only (R13.7, R2.5). Administrators: any portfolio that exists,
    or every customer's at once (portfolio_id=ALL)."""
    if not _is_admin(viewer_id):
        if portfolio_id is ALL:
            raise NotFound()  # customers never see anyone else's history
        portfolios.get_owned_portfolio(viewer_id, portfolio_id)
        return
    if portfolio_id is ALL:
        return
    with get_db().cursor() as cur:
        cur.execute("SELECT 1 FROM Portfolio WHERE portfolioID = %s", (portfolio_id,))
        found = cur.fetchone()
    get_db().commit()
    if found is None:
        raise NotFound()


def _local(stored: datetime) -> datetime:
    return stored.replace(tzinfo=timezone.utc).astimezone(market_tz())


# Every row also names its customer and account, for the administrators' "All customers" view.
_OWNER = (" JOIN Portfolio po ON po.portfolioID = h.portfolioID JOIN `User` u ON u.userID = po.userID")
_OWNER_COLUMNS = ", u.name AS customer_name, po.account_name AS own_name, po.account_number AS own_number"


def _where(portfolio_id: int | None, lo: datetime, hi: datetime) -> tuple[str, list]:
    sql, params = " WHERE h.`timestamp` >= %s AND h.`timestamp` < %s", [lo, hi]
    if portfolio_id is not ALL:
        sql += " AND h.portfolioID = %s"
        params.append(portfolio_id)
    return sql, params


def _owner(r: dict) -> dict:
    return {"customer": r["customer_name"], "account": f"{r['own_name']} {mask_last4(r['own_number'])}"}


def _ids(ids: list[int]) -> tuple[str, list]:
    return " WHERE h.historyID IN (" + ", ".join(["%s"] * len(ids)) + ")", list(ids)


def _trades(cur, ids: list[int]) -> list[tuple]:
    where, params = _ids(ids)
    cur.execute(
        "SELECT h.historyID, h.action, h.`timestamp`, t.quantity, t.price_per_share, t.total_amount, t.status,"
        " i.ticker_symbol" + _OWNER_COLUMNS + " FROM History h JOIN `Transaction` t ON t.transactionID = h.transactionID"
        " JOIN Issuer i ON i.issuerID = t.issuerID" + _OWNER + where,
        params,
    )
    found = []
    for r in cur.fetchall():
        verb, sign = ("Bought", -1) if r["action"] == "Buy" else ("Sold", 1)
        found.append((r["timestamp"], r["historyID"], HistoryRow(
            at=_local(r["timestamp"]), type=r["action"], symbol=r["ticker_symbol"],
            description=f"{verb} {shares(r['quantity'])} {r['ticker_symbol']} @ {money(r['price_per_share'])}",
            quantity=r["quantity"], price=r["price_per_share"], amount=sign * r["total_amount"], status=r["status"],
            **_owner(r),
        )))
    return found


def _transfers(cur, ids: list[int]) -> list[tuple]:
    where, params = _ids(ids)
    cur.execute(
        "SELECT h.historyID, h.action, h.`timestamp`, tr.amount, tr.status, b.nickname, b.account_number AS bank_number,"
        " p.account_name AS other_name, p.account_number AS other_number" + _OWNER_COLUMNS +
        " FROM History h JOIN Transfer tr ON tr.transferID = h.transferID"
        " LEFT JOIN BankAccount b ON b.bankAccountID = tr.bankAccountID"
        " LEFT JOIN Portfolio p ON tr.transfer_type = 'Internal'"
        "  AND p.portfolioID = IF(tr.from_portfolioID = h.portfolioID, tr.to_portfolioID, tr.from_portfolioID)"
        + _OWNER + where,
        params,
    )
    found = []
    for r in cur.fetchall():
        action = r["action"]
        if action in ("Deposit", "Withdrawal"):
            # Removed bank accounts still show here (R10.7). Only the last 4 digits (R10.5).
            bank = f"{r['nickname']} {mask_last4(decrypt_field(r['bank_number']))}"
            description = f"Deposit from {bank}" if action == "Deposit" else f"Withdrawal to {bank}"
        else:
            other = f"{r['other_name']} {mask_last4(r['other_number'])}"
            description = f"Transfer to {other}" if action == "Transfer out" else f"Transfer from {other}"  # R19.4
        sign = 1 if action in ("Deposit", "Transfer in") else -1
        found.append((r["timestamp"], r["historyID"], HistoryRow(
            at=_local(r["timestamp"]), type=action, symbol=None, description=description,
            quantity=None, price=None, amount=sign * r["amount"], status=r["status"], **_owner(r),
        )))
    return found


def _filter(portfolio_id: int | None, lo: datetime, hi: datetime, symbol: str | None) -> tuple[str, list]:
    """FROM/WHERE for the History rows that match. With a symbol, only that stock's trades."""
    where, params = _where(portfolio_id, lo, hi)
    sql = (" FROM History h LEFT JOIN `Transaction` t ON t.transactionID = h.transactionID"
           " LEFT JOIN Issuer i ON i.issuerID = t.issuerID" + where)
    if symbol:
        sql += " AND i.ticker_symbol = %s"
        params.append(symbol)
    return sql, params


def _count(cur, sql: str, params: list) -> int:
    cur.execute("SELECT COUNT(*) AS n" + sql, params)
    return cur.fetchone()["n"]


def _matching(cur, sql: str, params: list, limit: int | None = None, offset: int = 0) -> list[int]:
    """The matching historyIDs, newest first (optionally one page of them)."""
    query = "SELECT h.historyID" + sql + " ORDER BY h.`timestamp` DESC, h.historyID DESC"
    if limit is not None:
        query += " LIMIT %s OFFSET %s"
        params = [*params, limit, offset]
    cur.execute(query, params)
    return [r["historyID"] for r in cur.fetchall()]


def _rows(cur, ids: list[int]) -> list[HistoryRow]:
    """The rows for these historyIDs, in the same (newest first) order."""
    if not ids:
        return []
    by_id = {history_id: row for _, history_id, row in _trades(cur, ids) + _transfers(cur, ids)}
    return [by_id[i] for i in ids]


def _prepare(viewer_id: int, portfolio_id: int | None, start: date, end: date, symbol: str | None):
    _check_access(viewer_id, portfolio_id)
    symbol = stocks.normalize_symbol(symbol) if symbol and symbol.strip() else None
    return symbol, *_utc_bounds(start, end)


def search_history(viewer_id: int, portfolio_id: int | None, start: date, end: date,
                   symbol: str | None = None) -> list[HistoryRow]:
    """A portfolio's trades and transfers from `start` to `end` (Arizona dates, inclusive), newest first (R13.2).

    portfolio_id=ALL (None) lists every customer's, for administrators only (R13.6). With a symbol,
    only that stock's trades (transfers have no symbol). Raises NotFound for a portfolio the viewer
    may not see, and ValidationFailed("symbol") for a malformed symbol.
    """
    symbol, lo, hi = _prepare(viewer_id, portfolio_id, start, end, symbol)
    sql, params = _filter(portfolio_id, lo, hi, symbol)
    with get_db().cursor() as cur:
        rows = _rows(cur, _matching(cur, sql, params))
    get_db().commit()
    return rows


def history_page(viewer_id: int, portfolio_id: int | None, start: date, end: date, symbol: str | None = None,
                 page: int = 1, per_page: int = PER_PAGE) -> ListPage:
    """One page of search_history (user feedback: the list mustn't grow without end). Same rules and order;
    a page past the end shows the last page."""
    symbol, lo, hi = _prepare(viewer_id, portfolio_id, start, end, symbol)
    sql, params = _filter(portfolio_id, lo, hi, symbol)
    with get_db().cursor() as cur:
        total = _count(cur, sql, params)
        page, pages = clamp(page, total, per_page)
        rows = _rows(cur, _matching(cur, sql, params, limit=per_page, offset=(page - 1) * per_page))
    get_db().commit()
    return ListPage(rows=tuple(rows), page=page, pages=pages, total=total, per_page=per_page)


def today_in_market() -> date:
    return clock.now_utc().astimezone(market_tz()).date()
