"""Application settings, read from environment variables (see .env.example).

The app refuses to start when a required setting is missing or invalid, so a
misconfiguration fails loudly at startup instead of surfacing later.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.crypto import validate_key
from app.errors import ValidationFailed

APP_ENVS = ("development", "test", "production")

# Portfolio account types (R4.2). Brokerage only (D14); add more here if the team decides to.
ACCOUNT_TYPES: tuple[str, ...] = ("Brokerage",)

# Support panel contact details (D24, A20); override with SUPPORT_EMAIL and SUPPORT_HOURS.
DEFAULT_SUPPORT_EMAIL = "support@example.com"
DEFAULT_SUPPORT_HOURS = "Monday–Friday, 9:00 AM–5:00 PM Arizona time"
SUPPORT_HOURS_MAX = 200

REQUIRED = (
    "SECRET_KEY",
    "DB_HOST",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "FIELD_ENCRYPTION_KEYS",
)


# Production start-up guards (task 6.1; N1.4, N3.4, N3.6).
SECRET_KEY_MIN_LENGTH = 32
SECRET_KEY_MIN_DISTINCT = 10
KNOWN_WEAK_SECRET_KEYS = {"test-secret-key-not-for-real-use", "changeme", "change-me", "secret", "dev", "development"}
TRUE_WORDS = {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Raised when one or more settings are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    app_env: str
    secret_key: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_migrate_user: str | None
    db_migrate_password: str | None
    field_encryption_keys: tuple[str, ...]
    market_timezone: str
    price_tick_min_seconds: int
    price_tick_max_seconds: int
    price_daily_volatility_pct: Decimal
    price_max_tick_change_pct: Decimal
    stale_price_seconds: int
    login_max_failures: int
    login_lock_minutes: int
    remember_days: int
    temp_password_days: int
    support_email: str
    support_hours: str

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def _get(env: Mapping[str, str], key: str) -> str | None:
    """Return a stripped value, treating blank values as unset."""
    value = env.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _positive_int(env: Mapping[str, str], key: str, default: int, errors: list[str]) -> int:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{key} must be a whole number, got {raw!r}")
        return default
    if value <= 0:
        errors.append(f"{key} must be greater than 0, got {value}")
    return value


def _positive_decimal(env: Mapping[str, str], key: str, default: str, errors: list[str]) -> Decimal:
    raw = _get(env, key) or default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        errors.append(f"{key} must be a number, got {raw!r}")
        return Decimal(default)
    if not value.is_finite() or value <= 0:
        errors.append(f"{key} must be a number greater than 0, got {raw!r}")
        return Decimal(default)
    return value


def load_settings(env: Mapping[str, str]) -> Settings:
    """Build Settings from an environment mapping, or raise ConfigError listing every problem."""
    errors: list[str] = []

    for key in REQUIRED:
        if _get(env, key) is None:
            errors.append(f"missing required setting {key}")

    app_env = _get(env, "APP_ENV") or "production"
    if app_env not in APP_ENVS:
        errors.append(f"APP_ENV must be one of {', '.join(APP_ENVS)}, got {app_env!r}")

    keys = tuple(k.strip() for k in (_get(env, "FIELD_ENCRYPTION_KEYS") or "").split(",") if k.strip())
    for position, key in enumerate(keys, start=1):
        try:
            validate_key(key)
        except ValueError as exc:
            errors.append(f"FIELD_ENCRYPTION_KEYS entry {position} is {exc}")

    timezone = _get(env, "MARKET_TIMEZONE") or "America/Phoenix"
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"MARKET_TIMEZONE {timezone!r} is not a known time zone")

    tick_min = _positive_int(env, "PRICE_TICK_MIN_SECONDS", 30, errors)
    tick_max = _positive_int(env, "PRICE_TICK_MAX_SECONDS", 120, errors)
    if tick_min > tick_max:
        errors.append(
            f"PRICE_TICK_MIN_SECONDS ({tick_min}) must not be greater than PRICE_TICK_MAX_SECONDS ({tick_max})"
        )

    from app.services import validation  # imported here: the services package is not needed to load settings

    support_email = _get(env, "SUPPORT_EMAIL") or DEFAULT_SUPPORT_EMAIL
    try:
        support_email = validation.email(support_email, "SUPPORT_EMAIL")
    except ValidationFailed:
        errors.append(f"SUPPORT_EMAIL must be a valid email address, got {support_email!r}")
    support_hours = _get(env, "SUPPORT_HOURS") or DEFAULT_SUPPORT_HOURS
    if len(support_hours) > SUPPORT_HOURS_MAX:
        errors.append(f"SUPPORT_HOURS must be at most {SUPPORT_HOURS_MAX} characters")

    settings = Settings(
        app_env=app_env,
        secret_key=_get(env, "SECRET_KEY") or "",
        db_host=_get(env, "DB_HOST") or "",
        db_port=_positive_int(env, "DB_PORT", 3306, errors),
        db_name=_get(env, "DB_NAME") or "",
        db_user=_get(env, "DB_USER") or "",
        db_password=_get(env, "DB_PASSWORD") or "",
        db_migrate_user=_get(env, "DB_MIGRATE_USER"),
        db_migrate_password=_get(env, "DB_MIGRATE_PASSWORD"),
        field_encryption_keys=keys,
        market_timezone=timezone,
        price_tick_min_seconds=tick_min,
        price_tick_max_seconds=tick_max,
        price_daily_volatility_pct=_positive_decimal(env, "PRICE_DAILY_VOLATILITY_PCT", "1.5", errors),
        price_max_tick_change_pct=_positive_decimal(env, "PRICE_MAX_TICK_CHANGE_PCT", "1.0", errors),
        stale_price_seconds=_positive_int(env, "STALE_PRICE_SECONDS", 300, errors),
        login_max_failures=_positive_int(env, "LOGIN_MAX_FAILURES", 5, errors),
        login_lock_minutes=_positive_int(env, "LOGIN_LOCK_MINUTES", 15, errors),
        remember_days=_positive_int(env, "REMEMBER_DAYS", 30, errors),
        temp_password_days=_positive_int(env, "TEMP_PASSWORD_DAYS", 7, errors),  # A18
        support_email=support_email,
        support_hours=support_hours,
    )

    if app_env == "production":
        errors += production_problems(env)

    if errors:
        details = "\n".join(f"  - {e}" for e in errors)
        raise ConfigError(
            "Invalid configuration:\n"
            f"{details}\n"
            "Set these in the environment or in .env (copy .env.example to .env to start)."
        )
    return settings


def production_problems(env: Mapping[str, str]) -> list[str]:
    """What stops the app starting with APP_ENV=production (task 6.1). A missing SECRET_KEY is reported above."""
    problems = []
    secret = _get(env, "SECRET_KEY")
    if secret and (len(secret) < SECRET_KEY_MIN_LENGTH or len(set(secret)) < SECRET_KEY_MIN_DISTINCT
                   or secret.lower() in KNOWN_WEAK_SECRET_KEYS):
        problems.append(f"SECRET_KEY is too weak for production: use at least {SECRET_KEY_MIN_LENGTH} random characters "
                        "(see .env.example for how to generate one)")
    if (_get(env, "FLASK_DEBUG") or "").lower() in TRUE_WORDS:
        problems.append("debug mode must be off in production: unset FLASK_DEBUG (the debugger allows running code)")
    if (_get(env, "DB_USER") or "").lower() == "root":
        problems.append("DB_USER must not be root in production: use the least-privilege application account (trading_app)")
    return problems
