"""The random stock price generator (design: Price generator; R16, R7.8, A7, A8, A17, D12, D23).

Runs as `flask price-generator`. While the market is open it moves every listed
stock's price at random intervals (tick); after each trading day's close it saves
each portfolio's value once (snapshot_values). A MySQL named lock makes sure only
one generator runs at a time, even with several web servers (R16.6, D12).

Full-precision prices are kept in memory (TickState.exact) and only the rounded
value is stored, so cheap stocks don't freeze (see design "Price model").
Delisted stocks are not moved: their price stays where it was when delisted.
"""
from __future__ import annotations

import logging
import random
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask import Flask

from app import clock
from app.db import connect, get_db, transaction
from app.money import to_cents
from app.services import market
from app.services.pricing import PriceModel, next_price

log = logging.getLogger(__name__)

HEARTBEAT_KEY = "last_price_tick"


@dataclass
class TickState:
    """What the generator remembers between ticks."""
    exact: dict[int, Decimal] = field(default_factory=dict)  # issuerID -> unrounded price
    last_tick_utc: datetime | None = None


def _naive(at_utc: datetime) -> datetime:
    return at_utc.astimezone(timezone.utc).replace(tzinfo=None) if at_utc.tzinfo else at_utc


def trading_day_seconds(schedule: market.Schedule) -> int:
    open_s = schedule.open_time.hour * 3600 + schedule.open_time.minute * 60 + schedule.open_time.second
    close_s = schedule.close_time.hour * 3600 + schedule.close_time.minute * 60 + schedule.close_time.second
    return close_s - open_s


def tick(state: TickState, now_utc: datetime, rng: random.Random, model: PriceModel) -> int:
    """Move every listed stock's price once, in one transaction (R16.2-R16.4). Returns how many moved.

    Call only while the market is open; run() checks that (R16.5).
    """
    schedule = market.current_schedule()
    day_seconds = trading_day_seconds(schedule)
    today = now_utc.astimezone(market.market_tz()).date()
    elapsed = (now_utc - state.last_tick_utc).total_seconds() if state.last_tick_utc else model.tick_max_seconds
    now = _naive(now_utc)
    with transaction() as cur:
        cur.execute("SELECT issuerID, current_price FROM Issuer WHERE is_listed ORDER BY issuerID FOR UPDATE")
        stocks = cur.fetchall()
        for stock in stocks:
            issuer_id, stored = stock["issuerID"], stock["current_price"]
            exact = state.exact.get(issuer_id)
            if exact is None or to_cents(exact) != stored:
                exact = stored  # new stock, first tick, or the price was changed some other way: resync
            exact = next_price(exact, elapsed, day_seconds, rng, model)
            state.exact[issuer_id] = exact
            new_price = to_cents(exact)
            cur.execute("UPDATE Issuer SET current_price = %s WHERE issuerID = %s", (new_price, issuer_id))
            # The first tick of the day opens at the price before it: the previous close (A8).
            # Later ticks keep that open and only move the close (R16.4, R16.7).
            cur.execute(
                "INSERT INTO StockPrice (issuerID, price_date, open_price, close_price) VALUES (%s, %s, %s, %s)"
                " AS new ON DUPLICATE KEY UPDATE close_price = new.close_price",
                (issuer_id, today, stored, new_price),
            )
        cur.execute(
            "INSERT INTO SystemState (state_key, state_value, updated_at) VALUES (%s, %s, %s)"
            " AS new ON DUPLICATE KEY UPDATE state_value = new.state_value, updated_at = new.updated_at",
            (HEARTBEAT_KEY, now.isoformat(timespec="milliseconds"), now),
        )
    state.last_tick_utc = now_utc
    return len(stocks)


def last_tick(cursor=None) -> datetime | None:
    """When prices last moved (UTC, naive), or None if they never have."""
    sql = "SELECT updated_at FROM SystemState WHERE state_key = %s"
    if cursor is not None:
        cursor.execute(sql, (HEARTBEAT_KEY,))
        found = cursor.fetchone()
    else:
        with get_db().cursor() as cur:
            cur.execute(sql, (HEARTBEAT_KEY,))
            found = cur.fetchone()
        get_db().commit()
    return found["updated_at"] if found else None


def prices_stale(now_utc: datetime, stale_seconds: int) -> bool:
    """True while the market is open and prices haven't moved for `stale_seconds` (R7.8, A17)."""
    if not market.is_market_open(now_utc):
        return False
    moved = last_tick()
    return moved is None or _naive(now_utc) - moved > timedelta(seconds=stale_seconds)


def snapshot_due(now_utc: datetime) -> bool:
    """After the close of a trading day (D23); never while the market is forced open, since prices still move (D29)."""
    local = now_utc.astimezone(market.market_tz())
    schedule = market.current_schedule()
    if schedule.force_open:
        return False
    return market.is_trading_day(local.date(), schedule) and local.time() >= schedule.close_time


def snapshot_values(now_utc: datetime) -> int:
    """Save each portfolio's value for today's trading day, once, after the close (D23).

    Value = cash + each position's quantity x current (closing) price, rounded to the cent per
    position. Returns how many rows were written: 0 if it isn't due or today's snapshot exists.
    """
    if not snapshot_due(now_utc):
        return 0
    today = now_utc.astimezone(market.market_tz()).date()
    with transaction() as cur:
        cur.execute("SELECT EXISTS(SELECT 1 FROM PortfolioValueDaily WHERE value_date = %s) AS done", (today,))
        if cur.fetchone()["done"]:
            return 0
        cur.execute("SELECT portfolioID, cash_balance FROM Portfolio ORDER BY portfolioID")
        values = {r["portfolioID"]: [r["cash_balance"], r["cash_balance"]] for r in cur.fetchall()}
        cur.execute(
            "SELECT i.portfolioID, i.quantity, s.current_price"
            " FROM Inventory i JOIN Issuer s ON s.issuerID = i.issuerID"
        )
        for position in cur.fetchall():
            values[position["portfolioID"]][1] += to_cents(position["quantity"] * position["current_price"])
        rows = [(pid, today, cash, total) for pid, (cash, total) in values.items()]
        if rows:
            cur.executemany(
                "INSERT INTO PortfolioValueDaily (portfolioID, value_date, cash, total_value) VALUES (%s, %s, %s, %s)"
                " AS new ON DUPLICATE KEY UPDATE cash = new.cash, total_value = new.total_value",
                rows,
            )
    return len(rows)


def lock_name(database: str) -> str:
    """GET_LOCK names are server-wide, so include the database: dev and test generators don't block each other."""
    return f"price_generator:{database}"


def run(app: Flask, *, rng: random.Random | None = None, sleep: Callable[[float], None] = time_module.sleep,
        keep_going: Callable[[], bool] = lambda: True) -> bool:
    """The generator loop (DD6). Returns False at once if another generator holds the lock (R16.6)."""
    settings = app.config["SETTINGS"]
    rng = rng or random.Random()
    model = PriceModel.from_settings(settings)
    lock_conn = connect(settings)  # holds the named lock for the life of the process
    try:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0) AS got", (lock_name(settings.db_name),))
            if cur.fetchone()["got"] != 1:
                log.info("another price generator holds the lock; exiting")
                return False
        log.info("price generator started")
        state = TickState()
        while keep_going():
            sleep(rng.uniform(settings.price_tick_min_seconds, settings.price_tick_max_seconds))  # A7
            with app.app_context():
                try:
                    now_utc = clock.now_utc()
                    if market.is_market_open(now_utc):
                        tick(state, now_utc, rng, model)
                    elif snapshot_values(now_utc):
                        log.info("saved portfolio values for %s", now_utc.astimezone(market.market_tz()).date())
                except Exception:  # a database hiccup must not stop the market; try again next pass
                    log.exception("price generator pass failed")
        return True
    finally:
        lock_conn.close()  # closing the connection releases the named lock
