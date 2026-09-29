"""Price model math (task 1.6; property P6; R16.2, R16.3, A7, A9)."""
import math
import random
import statistics
from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.config import load_settings
from app.services.pricing import FLOOR, PriceModel, history_walk, next_price

D = Decimal
DAY = 8 * 3600  # 9:00-17:00
MODEL = PriceModel(daily_volatility=D("0.015"), max_tick_change=D("0.01"), tick_max_seconds=120)


def cents(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def simulate_daily_moves(start: Decimal, days: int, seed: int) -> list[float]:
    """Run the generator the way the worker will: random 30-120 s ticks, stored prices rounded to cents."""
    rng = random.Random(seed)
    exact, stored, moves = start, cents(start), []
    for _ in range(days):
        day_open, elapsed = stored, 0.0
        while True:
            dt = rng.uniform(30, 120)
            elapsed += dt
            if elapsed > DAY:
                break
            exact = next_price(exact, dt, DAY, rng, MODEL)
            stored = cents(exact)
        moves.append(math.log(stored / day_open))
    return moves


@pytest.mark.parametrize("start", [D("1.00"), D("5.00"), D("150.00")])
def test_p6_typical_daily_move_is_about_one_and_a_half_percent(start):  # P6, A7
    moves = simulate_daily_moves(start, days=250, seed=20260928)
    assert 0.013 <= statistics.pstdev(moves) <= 0.017


@given(
    exact=st.decimals(min_value=D("0.01"), max_value=D("100000"), places=6),
    seconds=st.floats(min_value=0, max_value=10**6, allow_nan=False),
    seed=st.integers(0, 2**32),
)
def test_p6_a_tick_never_breaks_the_floor_or_the_cap(exact, seconds, seed):  # P6, R16.3
    new = next_price(exact, seconds, DAY, random.Random(seed), MODEL)
    assert isinstance(new, Decimal)
    assert new >= FLOOR
    if new > FLOOR:  # unless the floor kicked in, the move is within the 1% cap
        assert abs((new / exact).ln()) <= MODEL.max_tick_change + D("1e-20")


def test_p6_cap_holds_even_with_an_absurd_volatility_setting():
    wild = PriceModel(daily_volatility=D("50"), max_tick_change=D("0.01"), tick_max_seconds=120)
    rng = random.Random(1)
    for _ in range(1000):
        new = next_price(D("100"), 120, DAY, rng, wild)
        assert D("100") * D("-0.01").exp() - D("1e-20") <= new <= D("100") * D("0.01").exp() + D("1e-20")


def test_overnight_gap_counts_as_one_ordinary_tick():  # SR03: prices don't move while closed
    overnight = next_price(D("100"), 16 * 3600, DAY, random.Random(7), MODEL)
    ordinary = next_price(D("100"), 120, DAY, random.Random(7), MODEL)
    assert overnight == ordinary


def test_no_elapsed_time_means_no_change():
    assert next_price(D("100"), 0, DAY, random.Random(1), MODEL) == D("100")


def test_same_seed_gives_the_same_prices():
    a = [next_price(D("50"), 75, DAY, random.Random(99), MODEL) for _ in range(3)]
    b = [next_price(D("50"), 75, DAY, random.Random(99), MODEL) for _ in range(3)]
    assert a == b


def test_cheap_stock_is_floored_not_negative_or_zero():
    rng = random.Random(3)
    price = D("0.01")
    for _ in range(500):
        price = next_price(price, 120, DAY, rng, MODEL)
        assert price >= FLOOR


def test_non_positive_price_is_refused():
    with pytest.raises(ValueError):
        next_price(D("0"), 60, DAY, random.Random(1), MODEL)


def test_history_walk_ends_at_the_entered_price_and_has_one_close_per_day():  # R14.6, A9
    closes = history_walk(D("123.45"), days=252, rng=random.Random(5), model=MODEL)
    assert len(closes) == 253
    assert closes[-1] == D("123.45")
    assert all(isinstance(c, Decimal) and c >= FLOOR for c in closes)


def test_history_walk_uses_the_same_daily_volatility():  # A9
    closes = history_walk(D("150"), days=2000, rng=random.Random(11), model=MODEL)
    moves = [float((closes[i + 1] / closes[i]).ln()) for i in range(len(closes) - 1)]
    assert 0.013 <= statistics.pstdev(moves) <= 0.017


def test_history_walk_with_no_days_is_just_the_price():
    assert history_walk(D("10"), 0, random.Random(1), MODEL) == [D("10")]


def test_model_from_settings_converts_percentages(valid_env):
    model = PriceModel.from_settings(load_settings(valid_env))
    assert model == PriceModel(daily_volatility=D("0.015"), max_tick_change=D("0.010"), tick_max_seconds=120)
