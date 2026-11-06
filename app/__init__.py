"""Application factory."""
from __future__ import annotations

import os
from collections.abc import Mapping

from dotenv import find_dotenv, load_dotenv
from flask import Flask, jsonify

from app.cli import init_cli
from app.config import ACCOUNT_TYPES, ConfigError, load_settings
from app.crypto import FieldCipher
from app.db import init_db
from app.errors import register_error_handlers
from app.logs import configure_logging
from app.security import init_security, production_hardening_problems


def create_app(env: Mapping[str, str] | None = None) -> Flask:
    """Build the Flask app.

    Settings come from ``env`` when given (tests pass it explicitly), otherwise
    from the process environment plus a ``.env`` file found from the current
    working directory. In production (APP_ENV=production already in the
    environment, as systemd sets it) a ``.env`` file is never read: the settings
    come only from /etc/trading/trading.env, so a stray .env can't change or
    break them (docs/deployment.md). Raises ConfigError if settings are missing
    or invalid.
    """
    if env is None:
        if os.environ.get("APP_ENV") != "production":
            dotenv_path = find_dotenv(usecwd=True)
            if dotenv_path:
                load_dotenv(dotenv_path)
        env = os.environ

    settings = load_settings(env)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.secret_key,
        TESTING=settings.app_env == "test",
        SETTINGS=settings,
        ACCOUNT_TYPES=ACCOUNT_TYPES,
    )
    app.extensions["field_cipher"] = FieldCipher(settings.field_encryption_keys)
    init_security(app, settings)
    register_error_handlers(app)
    init_db(app)
    init_cli(app)

    # Each blueprint is registered by the task that creates it (docs/tasks.md, Definition of done).
    from app.account import init_account
    from app.admin import init_admin
    from app.auth import init_auth, public
    from app.filters import init_filters
    from app.history import init_history
    from app.stocks import init_stocks
    from app.trade import init_trade
    from app.transfer import init_transfer

    init_filters(app)
    init_auth(app)
    init_account(app)
    init_admin(app)
    init_transfer(app)
    init_stocks(app)
    init_trade(app)
    init_history(app)

    configure_logging(app)
    if settings.is_production:
        problems = production_hardening_problems(app)
        if problems:
            details = "\n".join(f"  - {p}" for p in problems)
            raise ConfigError(f"The app can't start in production:\n{details}")

    @app.get("/health")
    @public
    def health():
        """For uptime monitoring (task 6.2): the app answers, and so does its database.

        A monitor gets 200 when both work, and 503 when the database doesn't, without the error's details
        (they go to the log only, N3.4)."""
        import pymysql

        from app.db import get_db

        try:
            with get_db().cursor() as cur:
                cur.execute("SELECT 1 AS up")
                cur.fetchone()
            get_db().commit()
        except pymysql.MySQLError as exc:
            app.logger.warning("health check: the database did not answer (MySQL error %s)",
                               exc.args[0] if exc.args else "unknown")
            return jsonify(status="error", database="unavailable"), 503
        return jsonify(status="ok", database="ok")

    return app