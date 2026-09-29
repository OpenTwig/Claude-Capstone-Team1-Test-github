"""Money helpers (task 1.2; N4.1, A3)."""
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.money import CENT, MAX_MONEY, SHARE, parse_money, parse_shares, to_cents, to_shares_floor

D = Decimal


@pytest.mark.parametrize(
    "value, expected",
    [
        ("0.005", "0.01"),      # half rounds up
        ("0.0049", "0.00"),
        ("0.015", "0.02"),
        ("0.025", "0.03"),      # half-up, not banker's rounding (which would give 0.02)
        ("2.675", "2.68"),
        ("99.994999", "99.99"),
        ("99.995", "100.00"),
        ("100", "100.00"),
        ("-0.005", "-0.01"),    # half away from zero
    ],
)
def test_to_cents_rounds_half_up(value, expected):  # N4.1
    assert to_cents(D(value)) == D(expected)
    assert to_cents(D(value)).as_tuple().exponent == -2


@pytest.mark.parametrize(
    "value, expected",
    [
        ("3.0003000300", "3.000300"),
        ("1.9999999", "1.999999"),   # never rounds up
        ("0.0000009", "0.000000"),
        ("2.5", "2.500000"),
    ],
)
def test_to_shares_floor_never_rounds_up(value, expected):  # A3
    assert to_shares_floor(D(value)) == D(expected)


@pytest.mark.parametrize("func", [to_cents, to_shares_floor])
def test_floats_are_refused(func):  # N4.1: money is never float
    with pytest.raises(TypeError, match="never use float"):
        func(0.1)


@pytest.mark.parametrize("func", [to_cents, to_shares_floor])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_values_are_refused(func, value):
    with pytest.raises(ValueError):
        func(D(value))


@given(st.decimals(min_value=D("-1e9"), max_value=D("1e9"), allow_nan=False, allow_infinity=False, places=10))
def test_to_cents_is_within_half_a_cent(value):
    assert abs(to_cents(value) - value) <= D("0.005")


@given(st.decimals(min_value=D("0"), max_value=D("1e9"), allow_nan=False, allow_infinity=False, places=12))
def test_to_shares_floor_is_at_most_one_increment_below(value):  # A3
    result = to_shares_floor(value)
    assert result <= value
    assert value - result < SHARE


@pytest.mark.parametrize(
    "text, expected",
    [("1", "1"), ("1.5", "1.5"), ("250.50", "250.50"), (" 42 ", "42"), ("0.01", "0.01"), ("99999999.99", "99999999.99")],
)
def test_parse_money_accepts_plain_amounts(text, expected):
    result = parse_money(text)
    assert result == D(expected)
    assert isinstance(result, Decimal)


@pytest.mark.parametrize(
    "text",
    ["", "   ", "abc", "-1", "+5", "0", "0.00", "1.001", "1e3", "NaN", "Infinity", "$5", ".5", "5.", "1 000",
     "100000000.00", "99999999.991", None,
     # Commas only as thousands separators (D27): these are misplaced, or a decimal comma.
     "1,00", "10,00", "1,0000", ",100", "100,", "1,,000", "1000,000", "1,000,00", "0,5", "1,000.001", "100,000,000.00"],
)
def test_parse_money_rejects_everything_else(text):  # N2.2
    with pytest.raises(ValueError):
        parse_money(text)


@pytest.mark.parametrize("text, expected", [
    ("1,000", "1000"), ("1,000.00", "1000.00"), ("25,000.5", "25000.5"), ("99,999,999.99", "99999999.99"), (" 1,234 ", "1234"),
])
def test_parse_money_accepts_thousands_separators(text, expected):  # D27 (user request, 2026-09-29)
    assert parse_money(text) == D(expected)


@pytest.mark.parametrize("text, expected", [
    ("1,000", "1000"), ("1,000,000", "1000000"), ("1,234.567891", "1234.567891"), ("999,999,999,999.999999", "999999999999.999999"),
])
def test_parse_shares_accepts_thousands_separators(text, expected):  # D27
    assert parse_shares(text) == D(expected)


@pytest.mark.parametrize("text", ["1,00", "10,00", ",100", "1,,000", "1000,000", "2,5", "1,000.0000001"])
def test_parse_shares_rejects_misplaced_commas(text):  # D27
    with pytest.raises(ValueError):
        parse_shares(text)


@pytest.mark.parametrize("text", ["٣٠٠", "３００", "1٫50", "𝟓"])  # Arabic-Indic, full-width, math digits
def test_only_ascii_digits_are_accepted(text):
    with pytest.raises(ValueError):
        parse_money(text)
    with pytest.raises(ValueError):
        parse_shares(text)


def test_parse_money_upper_limit_matches_the_column():
    assert parse_money(str(MAX_MONEY)) == MAX_MONEY
    with pytest.raises(ValueError) as exc:
        parse_money("100000000")
    assert str(exc.value) == "The largest amount you can enter is $99,999,999.99."  # the limit, not "too large"


@pytest.mark.parametrize("text, expected", [("10", "10"), ("2.5", "2.5"), ("0.000001", "0.000001")])
def test_parse_shares_accepts_up_to_six_decimals(text, expected):  # A3
    assert parse_shares(text) == D(expected)


@pytest.mark.parametrize("text", ["", "0", "-1", "1.0000001", "1e2", "NaN", "abc", "1,5", "1000000000000"])
def test_parse_shares_rejects_invalid_quantities(text):
    with pytest.raises(ValueError):
        parse_shares(text)


@given(st.decimals(min_value=CENT, max_value=MAX_MONEY, places=2))
def test_any_valid_amount_round_trips_through_parse_money(amount):
    assert parse_money(str(amount)) == amount


def test_parse_shares_upper_limit_is_stated():
    with pytest.raises(ValueError) as exc:
        parse_shares("1000000000000")
    assert str(exc.value) == "The largest number of shares you can enter is 999,999,999,999.999999."
