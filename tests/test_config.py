"""Settings loading and validation (design: Configuration; task 0.1)."""
from decimal import Decimal

import pytest

from app.config import REQUIRED, ConfigError, load_settings
from tests.conftest import STRONG_SECRET_KEY


def test_valid_settings_load_with_typed_values_and_defaults(valid_env):
    s = load_settings(valid_env)
    assert s.app_env == "test"
    assert s.db_port == 3306
    assert s.field_encryption_keys == tuple(valid_env["FIELD_ENCRYPTION_KEYS"].split(","))
    # Defaults from design "Configuration"
    assert s.market_timezone == "America/Phoenix"  # D6
    assert (s.price_tick_min_seconds, s.price_tick_max_seconds) == (30, 120)  # A7
    assert s.price_daily_volatility_pct == Decimal("1.5")  # A7
    assert s.price_max_tick_change_pct == Decimal("1.0")  # A7
    assert s.stale_price_seconds == 300  # A17
    assert (s.login_max_failures, s.login_lock_minutes) == (5, 15)  # A1
    assert s.remember_days == 30  # A2
    assert isinstance(s.price_daily_volatility_pct, Decimal)


def test_app_env_defaults_to_production_the_strictest_mode(valid_env):
    del valid_env["APP_ENV"]
    valid_env["SECRET_KEY"] = STRONG_SECRET_KEY  # production refuses the test suite's weak key (task 6.1)
    s = load_settings(valid_env)
    assert s.app_env == "production"
    assert s.is_production


@pytest.mark.parametrize("key", REQUIRED)
def test_missing_required_setting_is_named_in_the_error(valid_env, key):  # N1.4
    del valid_env[key]
    with pytest.raises(ConfigError) as exc:
        load_settings(valid_env)
    assert f"missing required setting {key}" in str(exc.value)
    assert ".env.example" in str(exc.value)


def test_blank_value_counts_as_missing(valid_env):
    valid_env["SECRET_KEY"] = "   "
    with pytest.raises(ConfigError, match="missing required setting SECRET_KEY"):
        load_settings(valid_env)


def test_every_problem_is_reported_together(valid_env):
    del valid_env["SECRET_KEY"]
    del valid_env["DB_PASSWORD"]
    valid_env["DB_PORT"] = "abc"
    with pytest.raises(ConfigError) as exc:
        load_settings(valid_env)
    message = str(exc.value)
    assert "SECRET_KEY" in message
    assert "DB_PASSWORD" in message
    assert "DB_PORT must be a whole number" in message


def test_invalid_app_env_is_rejected(valid_env):
    valid_env["APP_ENV"] = "staging"
    with pytest.raises(ConfigError, match="APP_ENV must be one of"):
        load_settings(valid_env)


@pytest.mark.parametrize("value", ["abc", "3.5", "0", "-1"])
def test_invalid_integer_settings_are_rejected(valid_env, value):
    valid_env["STALE_PRICE_SECONDS"] = value
    with pytest.raises(ConfigError, match="STALE_PRICE_SECONDS"):
        load_settings(valid_env)


@pytest.mark.parametrize("value", ["abc", "0", "-1.5", "NaN", "Infinity"])
def test_invalid_decimal_settings_are_rejected(valid_env, value):
    valid_env["PRICE_DAILY_VOLATILITY_PCT"] = value
    with pytest.raises(ConfigError, match="PRICE_DAILY_VOLATILITY_PCT"):
        load_settings(valid_env)


def test_tick_minimum_above_maximum_is_rejected(valid_env):
    valid_env["PRICE_TICK_MIN_SECONDS"] = "200"
    valid_env["PRICE_TICK_MAX_SECONDS"] = "120"
    with pytest.raises(ConfigError, match="PRICE_TICK_MIN_SECONDS"):
        load_settings(valid_env)


def test_unknown_time_zone_is_rejected(valid_env):
    valid_env["MARKET_TIMEZONE"] = "Mars/Olympus_Mons"
    with pytest.raises(ConfigError, match="MARKET_TIMEZONE"):
        load_settings(valid_env)


def test_encryption_keys_ignore_blank_entries(valid_env):
    key_a, key_b = valid_env["FIELD_ENCRYPTION_KEYS"].split(",")
    valid_env["FIELD_ENCRYPTION_KEYS"] = f" {key_a} , ,{key_b},"
    assert load_settings(valid_env).field_encryption_keys == (key_a, key_b)


@pytest.mark.parametrize("bad_key", ["not-a-key", "short=", "a" * 44])
def test_invalid_encryption_key_is_rejected_at_startup(valid_env, bad_key):  # N1.3
    valid_env["FIELD_ENCRYPTION_KEYS"] = valid_env["FIELD_ENCRYPTION_KEYS"] + "," + bad_key
    with pytest.raises(ConfigError, match="FIELD_ENCRYPTION_KEYS entry 3 is not a valid Fernet key"):
        load_settings(valid_env)


def test_migration_credentials_are_optional_at_startup(valid_env):
    s = load_settings(valid_env)
    assert s.db_migrate_user is None and s.db_migrate_password is None

# --- Support panel contact details (D24, A20) ------------------------------------------------------

def test_support_contact_details_have_defaults(valid_env):
    settings = load_settings(valid_env)
    assert settings.support_email == "support@example.com"
    assert settings.support_hours == "Monday–Friday, 9:00 AM–5:00 PM Arizona time"


def test_support_contact_details_come_from_the_environment(valid_env):  # A20
    settings = load_settings({**valid_env, "SUPPORT_EMAIL": " Help@Broker.Example ", "SUPPORT_HOURS": " 24/7 "})
    assert (settings.support_email, settings.support_hours) == ("help@broker.example", "24/7")


@pytest.mark.parametrize("value", ["not-an-email", "a@b", "<script>@x.com"])
def test_invalid_support_email_is_rejected_at_startup(valid_env, value):
    with pytest.raises(ConfigError, match="SUPPORT_EMAIL must be a valid email address"):
        load_settings({**valid_env, "SUPPORT_EMAIL": value})


def test_overlong_support_hours_are_rejected(valid_env):
    with pytest.raises(ConfigError, match="SUPPORT_HOURS"):
        load_settings({**valid_env, "SUPPORT_HOURS": "x" * 201})
