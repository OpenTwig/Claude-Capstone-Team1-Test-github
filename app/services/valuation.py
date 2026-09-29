"""Market data and account valuation (design: Valuation and market data; R5, R6, R7, A8, A14,
A19, D18, D20, D22, D23, D26).

Prices and changes are Decimal. "Previous close" is the close of the latest StockPrice row
before today (Arizona date); a stock with no earlier row uses its current price (change 0).
The Stocks pages show listed stocks only (D20); Summary and Positions value whatever a
customer holds, delisted or not, at its last price.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from app import clock
from app.db import get_db
from app.money import CENT, to_cents
from app.services import portfolios, stocks
from app.services.paging import PER_PAGE, ListPage, clamp
from app.services.market import market_tz
from app.services.types import (
    AccountSummary,
    AdminStockRow,
    PortfolioRow,
    PortfolioSummaryRow,
    PositionRow,
    PositionsView,
    StockDetail,
    StockMover,
    StockRow,
    ValueTotals,
)

SPARKLINE_DAYS = 30
CHART_DAYS = 365
PANEL_SIZE = 5

_MOVER_SQL = (
    "SELECT i.issuerID, i.ticker_symbol, i.company_name, i.current_price,"
    " (SELECT sp.close_price FROM StockPrice sp WHERE sp.issuerID = i.issuerID AND sp.price_date < %s"
    "  ORDER BY sp.price_date DESC LIMIT 1) AS previous_close"
    " FROM Issuer i WHERE i.is_listed"
)


def market_today(at_utc: datetime | None = None) -> date:
    return (at_utc or clock.now_utc()).astimezone(market_tz()).date()


def _ratio(change: Decimal, previous: Decimal) -> Decimal:
    return change / previous if previous else Decimal(0)


def _mover(r: dict, sparkline=(), trades_today: int = 0) -> StockMover:
    price = r["current_price"]
    previous = r["previous_close"] if r["previous_close"] is not None else price
    change = price - previous
    return StockMover(
        issuer_id=r["issuerID"], symbol=r["ticker_symbol"], company_name=r["company_name"], price=price,
        previous_close=previous, change=change,
        change_pct=(_ratio(change, previous) * 100).quantize(CENT, rounding=ROUND_HALF_UP),
        sparkline=tuple(sparkline), trades_today=trades_today,
    )


def _rows(today: date, issuer_ids: list[int] | None = None) -> list[dict]:
    sql, params = _MOVER_SQL, [today]
    if issuer_ids is not None:
        if not issuer_ids:
            return []
        sql += " AND i.issuerID IN (" + ", ".join(["%s"] * len(issuer_ids)) + ")"
        params += issuer_ids
    with get_db().cursor() as cur:
        cur.execute(sql + " ORDER BY i.ticker_symbol", params)
        rows = list(cur.fetchall())  # PyMySQL returns () when nothing matches
    get_db().commit()
    return rows


def sparklines(issuer_ids: list[int], today: date, days: int = SPARKLINE_DAYS) -> dict[int, list[Decimal]]:
    """Each stock's daily closes over the last `days` calendar days, oldest first."""
    if not issuer_ids:
        return {}
    with get_db().cursor() as cur:
        cur.execute(
            "SELECT issuerID, close_price FROM StockPrice WHERE price_date > %s AND price_date <= %s"
            " AND issuerID IN (" + ", ".join(["%s"] * len(issuer_ids)) + ") ORDER BY issuerID, price_date",
            [today - timedelta(days=days), today, *issuer_ids],
        )
        rows = cur.fetchall()
    get_db().commit()
    series: dict[int, list[Decimal]] = {i: [] for i in issuer_ids}
    for r in rows:
        series[r["issuerID"]].append(r["close_price"])
    return series


def _with_sparklines(rows: list[dict], today: date, trades: dict[int, int] | None = None) -> list[StockMover]:
    lines = sparklines([r["issuerID"] for r in rows], today)
    return [_mover(r, lines[r["issuerID"]], (trades or {}).get(r["issuerID"], 0)) for r in rows]


def top_gainers(limit: int = PANEL_SIZE, at_utc: datetime | None = None) -> list[StockMover]:
    """The listed stocks with the highest % change since the previous close (A14); ties by symbol."""
    today = market_today(at_utc)
    rows = _rows(today)
    def pct(r):
        previous = r["previous_close"] if r["previous_close"] is not None else r["current_price"]
        return _ratio(r["current_price"] - previous, previous)
    rows.sort(key=lambda r: (-pct(r), r["ticker_symbol"]))
    return _with_sparklines(rows[:limit], today)


def trending(limit: int = PANEL_SIZE, at_utc: datetime | None = None) -> list[StockMover]:
    """The listed stocks with the most filled trades since the start of today, Arizona time (D18, A19).

    Stocks with no trades today are left out; ties are broken by symbol.
    """
    now_utc = at_utc or clock.now_utc()
    today = market_today(now_utc)
    start_utc = datetime.combine(today, time.min, market_tz()).astimezone(timezone.utc).replace(tzinfo=None)
    with get_db().cursor() as cur:
        cur.execute(
            "SELECT t.issuerID, COUNT(*) AS trades FROM `Transaction` t JOIN Issuer i ON i.issuerID = t.issuerID"
            " WHERE i.is_listed AND t.status = 'Filled' AND t.`timestamp` >= %s"
            " GROUP BY t.issuerID, i.ticker_symbol ORDER BY trades DESC, i.ticker_symbol LIMIT %s",
            (start_utc, limit),
        )
        counted = cur.fetchall()
    get_db().commit()
    trades = {r["issuerID"]: r["trades"] for r in counted}
    by_id = {r["issuerID"]: r for r in _rows(today, list(trades))}
    return _with_sparklines([by_id[i] for i in trades if i in by_id], today, trades)


def search(query: str | None, at_utc: datetime | None = None) -> list[StockMover]:
    """Search results with prices and day changes (R7.1, R7.5); listed stocks only (R7.9)."""
    found = stocks.search_stocks(query)
    today = market_today(at_utc)
    return _with_sparklines(_rows(today, [s.issuer_id for s in found]), today)


def stock_detail(symbol: str | None, at_utc: datetime | None = None) -> StockDetail:
    """The detail view for a listed stock (R7.4); SymbolNotFound for unknown or delisted ones (D20)."""
    stock = stocks.get_stock(symbol)
    today = market_today(at_utc)
    row = _rows(today, [stock.issuer_id])[0]
    with get_db().cursor() as cur:
        cur.execute(
            "SELECT price_date, open_price, close_price FROM StockPrice"
            " WHERE issuerID = %s AND price_date > %s AND price_date <= %s ORDER BY price_date",
            (stock.issuer_id, today - timedelta(days=CHART_DAYS), today),
        )
        history = cur.fetchall()
    get_db().commit()
    open_price = history[-1]["open_price"] if history and history[-1]["price_date"] == today else None
    return StockDetail(
        mover=_mover(row, [h["close_price"] for h in history[-SPARKLINE_DAYS:]]),
        exchange=stock.exchange,
        description=stock.description,
        open_price=open_price,
        history=tuple((h["price_date"], h["close_price"]) for h in history),
    )


# --- Account valuation: Summary and Positions (R5, R6, D23, D26) ----------------------------------------

ZERO = Decimal("0.00")
VALUE_CHART_DAYS = 365


def percent(amount: Decimal, base: Decimal) -> Decimal:
    """amount / base x 100, to 2 places; 0.00 when the base is zero or less (D26)."""
    if base <= 0:
        return ZERO
    return (amount / base * 100).quantize(CENT, rounding=ROUND_HALF_UP)


def _placeholders(ids) -> str:
    return ", ".join(["%s"] * len(ids))


def _positions(portfolio_ids: list[int], today: date) -> dict[int, list[PositionRow]]:
    """Every position in these portfolios, valued at the current price (Calculations table)."""
    found: dict[int, list[PositionRow]] = defaultdict(list)
    if not portfolio_ids:
        return found
    with get_db().cursor() as cur:
        cur.execute(
            "SELECT inv.portfolioID, inv.issuerID, inv.quantity, inv.average_cost, inv.reinvest,"
            " i.ticker_symbol, i.company_name, i.current_price, i.is_listed,"
            " (SELECT sp.close_price FROM StockPrice sp WHERE sp.issuerID = i.issuerID AND sp.price_date < %s"
            "  ORDER BY sp.price_date DESC LIMIT 1) AS previous_close"
            " FROM Inventory inv JOIN Issuer i ON i.issuerID = inv.issuerID"
            f" WHERE inv.portfolioID IN ({_placeholders(portfolio_ids)}) ORDER BY i.ticker_symbol",
            [today, *portfolio_ids],
        )
        rows = cur.fetchall()
    get_db().commit()
    for r in rows:
        price, quantity = r["current_price"], r["quantity"]
        previous = r["previous_close"] if r["previous_close"] is not None else price
        change = price - previous
        market_value = to_cents(quantity * price)
        cost_basis = to_cents(quantity * r["average_cost"])
        found[r["portfolioID"]].append(PositionRow(
            issuer_id=r["issuerID"], symbol=r["ticker_symbol"], company_name=r["company_name"],
            is_listed=bool(r["is_listed"]), quantity=quantity, price=price, price_change=change,
            price_change_pct=percent(change, previous), market_value=market_value,
            day_change=to_cents(quantity * change), average_cost=r["average_cost"], cost_basis=cost_basis,
            gain_loss=market_value - cost_basis, reinvest=bool(r["reinvest"]),
        ))
    return found


def _net_money_in(portfolio_ids: list[int], year_start: date) -> dict[int, Decimal]:
    """Completed deposits and moves in, minus withdrawals and moves out, since `year_start` (D23)."""
    net: dict[int, Decimal] = defaultdict(lambda: ZERO)
    if not portfolio_ids:
        return net
    marks = _placeholders(portfolio_ids)
    with get_db().cursor() as cur:
        cur.execute(
            "SELECT to_portfolioID AS pid, SUM(amount) AS total FROM Transfer WHERE status = 'Completed'"
            f" AND transfer_date >= %s AND to_portfolioID IN ({marks}) GROUP BY to_portfolioID",
            [year_start, *portfolio_ids],
        )
        for r in cur.fetchall():
            net[r["pid"]] += r["total"]
        cur.execute(
            "SELECT from_portfolioID AS pid, SUM(amount) AS total FROM Transfer WHERE status = 'Completed'"
            f" AND transfer_date >= %s AND from_portfolioID IN ({marks}) GROUP BY from_portfolioID",
            [year_start, *portfolio_ids],
        )
        for r in cur.fetchall():
            net[r["pid"]] -= r["total"]
    get_db().commit()
    return net


def _start_values(portfolio_ids: list[int], year_start: date) -> dict[int, Decimal]:
    """Each portfolio's value at its last snapshot before 1 January; $0.00 without one (D23)."""
    start: dict[int, Decimal] = defaultdict(lambda: ZERO)
    if not portfolio_ids:
        return start
    with get_db().cursor() as cur:
        cur.execute(
            "SELECT v.portfolioID, v.total_value FROM PortfolioValueDaily v"
            " JOIN (SELECT portfolioID, MAX(value_date) AS last_day FROM PortfolioValueDaily"
            f"       WHERE value_date < %s AND portfolioID IN ({_placeholders(portfolio_ids)}) GROUP BY portfolioID) l"
            " ON l.portfolioID = v.portfolioID AND l.last_day = v.value_date",
            [year_start, *portfolio_ids],
        )
        for r in cur.fetchall():
            start[r["portfolioID"]] = r["total_value"]
    get_db().commit()
    return start


def _chart(portfolio_ids: list[int], today: date, live_value: Decimal) -> tuple[tuple[date, Decimal], ...]:
    """Daily snapshots over the last year, added across the portfolios, then today's live value (D23)."""
    points: dict[date, Decimal] = {}
    if portfolio_ids:
        with get_db().cursor() as cur:
            cur.execute(
                "SELECT value_date, SUM(total_value) AS total FROM PortfolioValueDaily"
                f" WHERE value_date > %s AND value_date < %s AND portfolioID IN ({_placeholders(portfolio_ids)})"
                " GROUP BY value_date ORDER BY value_date",
                [today - timedelta(days=VALUE_CHART_DAYS), today, *portfolio_ids],
            )
            points = {r["value_date"]: r["total"] for r in cur.fetchall()}
        get_db().commit()
    points[today] = live_value
    return tuple(sorted(points.items()))


def _totals(cash: Decimal, held: list[PositionRow], start_value: Decimal, net_in: Decimal) -> ValueTotals:
    """A portfolio's totals from its cash and positions (Calculations table, D23, D26)."""
    positions_value = sum((p.market_value for p in held), ZERO)
    day_change = sum((p.day_change for p in held), ZERO)
    cost_basis = sum((p.cost_basis for p in held), ZERO)
    total_value = cash + positions_value
    ytd_change = total_value - start_value - net_in
    gain_loss = positions_value - cost_basis
    return ValueTotals(
        cash=cash, positions_value=positions_value, total_value=total_value,
        day_change=day_change, day_change_pct=percent(day_change, total_value - day_change),
        ytd_change=ytd_change, ytd_change_pct=percent(ytd_change, start_value + net_in),
        cost_basis=cost_basis, gain_loss=gain_loss, gain_loss_pct=percent(gain_loss, cost_basis),
    )


def _sum_totals(parts: list[ValueTotals], ytd_base: Decimal) -> ValueTotals:
    """Several portfolios' totals added up; percentages come from the summed amounts (D26)."""
    def add(name):
        return sum((getattr(t, name) for t in parts), ZERO)
    total_value, day_change, ytd_change = add("total_value"), add("day_change"), add("ytd_change")
    cost_basis, gain_loss = add("cost_basis"), add("gain_loss")
    return ValueTotals(
        cash=add("cash"), positions_value=add("positions_value"), total_value=total_value,
        day_change=day_change, day_change_pct=percent(day_change, total_value - day_change),
        ytd_change=ytd_change, ytd_change_pct=percent(ytd_change, ytd_base),
        cost_basis=cost_basis, gain_loss=gain_loss, gain_loss_pct=percent(gain_loss, cost_basis),
    )


def account_summary(user_id: int, at_utc: datetime | None = None) -> AccountSummary:
    """Every portfolio's values and the Investments Total (R5.1, R5.3, R5.4), with the value chart (R5.2)."""
    owned = portfolios.list_portfolios(user_id)
    ids = [p.portfolio_id for p in owned]
    today = market_today(at_utc)
    year_start = date(today.year, 1, 1)
    held, net_in, start = _positions(ids, today), _net_money_in(ids, year_start), _start_values(ids, year_start)
    rows = tuple(
        PortfolioSummaryRow(p, _totals(p.cash_balance, held[p.portfolio_id], start[p.portfolio_id],
                                       net_in[p.portfolio_id]))
        for p in owned
    )
    ytd_base = sum((start[i] + net_in[i] for i in ids), ZERO)
    totals = _sum_totals([r.totals for r in rows], ytd_base)
    return AccountSummary(rows=rows, totals=totals, chart=_chart(ids, today, totals.total_value))


def positions(user_id: int, portfolio_id: int, at_utc: datetime | None = None) -> PositionsView:
    """One portfolio's positions and values (R6). Someone else's portfolio is NotFound (R2.5)."""
    portfolio: PortfolioRow = portfolios.get_owned_portfolio(user_id, portfolio_id)
    today = market_today(at_utc)
    year_start = date(today.year, 1, 1)
    held = _positions([portfolio_id], today)[portfolio_id]
    totals = _totals(portfolio.cash_balance, held, _start_values([portfolio_id], year_start)[portfolio_id],
                     _net_money_in([portfolio_id], year_start)[portfolio_id])
    equities = _totals(ZERO, held, ZERO, ZERO)  # the Equities group's Total row: no cash, no YTD
    return PositionsView(portfolio=portfolio, positions=tuple(held), equities=equities, totals=totals,
                         chart=_chart([portfolio_id], today, totals.total_value))


def admin_stocks(page: int = 1, per_page: int = PER_PAGE, at_utc: datetime | None = None) -> ListPage:
    """One page of every stock (AdminStockRow), listed first then delisted, by symbol, with its change
    since the previous close, for administrators (R14.7). A page past the end shows the last page."""
    today = market_today(at_utc)
    with get_db().cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM Issuer")
        total = cur.fetchone()["n"]
        page, pages = clamp(page, total, per_page)
        cur.execute(
            "SELECT i.issuerID, i.ticker_symbol, i.company_name, i.exchange, i.current_price, i.description,"
            " i.shares_available, i.is_listed,"
            " (SELECT sp.close_price FROM StockPrice sp WHERE sp.issuerID = i.issuerID AND sp.price_date < %s"
            "  ORDER BY sp.price_date DESC LIMIT 1) AS previous_close"
            " FROM Issuer i ORDER BY i.is_listed DESC, i.ticker_symbol LIMIT %s OFFSET %s",
            (today, per_page, (page - 1) * per_page),
        )
        rows = cur.fetchall()
    get_db().commit()
    found = []
    for r in rows:
        price = r["current_price"]
        previous = r["previous_close"] if r["previous_close"] is not None else price
        found.append(AdminStockRow(
            stock=StockRow(issuer_id=r["issuerID"], symbol=r["ticker_symbol"], company_name=r["company_name"],
                           exchange=r["exchange"], current_price=price, description=r["description"],
                           shares_available=r["shares_available"], is_listed=bool(r["is_listed"])),
            change_pct=percent(price - previous, previous),
        ))
    return ListPage(rows=tuple(found), page=page, pages=pages, total=total, per_page=per_page)
