"""Random price model (design: Price generator, "Price model"; R16.2, R16.3, A7, A9).

Prices follow a random walk (geometric Brownian motion) sized so a typical
stock moves about PRICE_DAILY_VOLATILITY_PCT per trading day, whatever its
price and however far apart ticks are. next_price() and history_walk() are pure:
they return full-precision Decimal prices, and callers round to cents only when
storing. previous_close() reads the database.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

FLOOR = Decimal("0.01")


@dataclass(frozen=True)
class PriceModel:
    daily_volatility: Decimal  # fraction: 0.015 means 1.5% per trading day (one standard deviation)
    max_tick_change: Decimal   # fraction: largest log move allowed in one tick (safety cap)
    tick_max_seconds: int      # elapsed time beyond this is not counted (overnight gaps)

    @classmethod
    def from_settings(cls, settings) -> "PriceModel":
        return cls(
            daily_volatility=settings.price_daily_volatility_pct / 100,
            max_tick_change=settings.price_max_tick_change_pct / 100,
            tick_max_seconds=settings.price_tick_max_seconds,
        )


def _gauss(rng: random.Random, sigma: Decimal) -> Decimal:
    return Decimal(repr(rng.gauss(0.0, float(sigma))))


def next_price(
    exact: Decimal,
    seconds_elapsed: float,
    trading_day_seconds: int,
    rng: random.Random,
    model: PriceModel,
) -> Decimal:
    """One tick: the new full-precision price after `seconds_elapsed` of trading time."""
    if exact <= 0:
        raise ValueError("price must be positive")
    elapsed = min(Decimal(repr(float(seconds_elapsed))), Decimal(model.tick_max_seconds))
    if elapsed <= 0:
        return max(FLOOR, exact)
    sigma = model.daily_volatility * (elapsed / Decimal(trading_day_seconds)).sqrt()
    log_return = max(-model.max_tick_change, min(model.max_tick_change, _gauss(rng, sigma)))
    return max(FLOOR, exact * log_return.exp())


def previous_close(issuer_id: int, today: date) -> Decimal:
    """Close of the most recent trading day before `today`; the current price if there is none (A8)."""
    from app.db import get_db

    with get_db().cursor() as cur:
        cur.execute(
            "SELECT close_price FROM StockPrice WHERE issuerID = %s AND price_date < %s"
            " ORDER BY price_date DESC LIMIT 1",
            (issuer_id, today),
        )
        found = cur.fetchone()
        if found is None:
            cur.execute("SELECT current_price FROM Issuer WHERE issuerID = %s", (issuer_id,))
            found = {"close_price": cur.fetchone()["current_price"]}
    get_db().commit()
    return found["close_price"]


def history_walk(price: Decimal, days: int, rng: random.Random, model: PriceModel) -> list[Decimal]:
    """Walk backwards from `price` to make `days` of past daily closes (Randomize History, R14.6, A9).

    Returns days + 1 full-precision closes, oldest first; the last one equals
    `price`. Past day k (1..days) has open = closes[k-1] and close = closes[k].
    """
    if days < 0:
        raise ValueError("days must not be negative")
    closes = [price]
    for _ in range(days):
        closes.append(max(FLOOR, closes[-1] / _gauss(rng, model.daily_volatility).exp()))
    closes.reverse()
    return closes
