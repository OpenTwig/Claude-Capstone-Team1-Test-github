"""Database access (design: Core modules, db.py; DD1).

One PyMySQL connection per request, stored on flask.g and closed at teardown.
All SQL uses %s placeholders; user values are never placed in SQL text (N2.1).
Every connection runs with time_zone '+00:00' so CURRENT_TIMESTAMP(3) is UTC (DD7).
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

import pymysql
from flask import Flask, current_app, g
from pymysql.cursors import DictCursor

from app.config import Settings

log = logging.getLogger(__name__)

LOCK_WAIT_TIMEOUT = 1205
DEADLOCK = 1213
T = TypeVar("T")


def connect(
    settings: Settings,
    *,
    user: str | None = None,
    password: str | None = None,
    database: str | None = None,
) -> pymysql.connections.Connection:
    """Open a connection. Defaults to the app account (trading_app) and the app database."""
    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=user or settings.db_user,
        password=password if password is not None else settings.db_password,
        database=database or settings.db_name,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        init_command="SET time_zone = '+00:00'",
        connect_timeout=10,
    )


def get_db() -> pymysql.connections.Connection:
    """The current request's connection, opened on first use."""
    if "db" not in g:
        g.db = connect(current_app.config["SETTINGS"])
    return g.db


def close_db(exc: BaseException | None = None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        try:
            conn.rollback()  # never leave an open transaction on a pooled or reused connection
        finally:
            conn.close()


ISOLATION_LEVELS = ("READ COMMITTED",)


@contextmanager
def transaction(
    conn: pymysql.connections.Connection | None = None, isolation: str | None = None
) -> Iterator[DictCursor]:
    """BEGIN ... COMMIT, or ROLLBACK if anything inside raises. Yields a dict cursor.

    `isolation` sets the level for this one transaction. Trades use READ COMMITTED: under
    MySQL's default (REPEATABLE READ), `SELECT ... FOR UPDATE` on a row that doesn't exist yet
    takes a gap lock, and two customers opening new positions at once can deadlock.
    """
    conn = conn or get_db()
    if isolation is not None:
        if isolation not in ISOLATION_LEVELS:
            raise ValueError(f"unsupported isolation level: {isolation!r}")
        conn.commit()  # end any read-only snapshot, since the level can't change mid-transaction
        with conn.cursor() as cur:
            cur.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")  # sql-safe: a keyword, checked against ISOLATION_LEVELS above
    conn.begin()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        cursor.close()


def retry_once_on_lock_conflict(func: Callable[..., T]) -> Callable[..., T]:
    """Run a service call once more if MySQL chose it as a deadlock victim or its lock wait timed out
    (design: Error handling). Safe because the failed attempt's transaction was rolled back in full."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except pymysql.err.OperationalError as exc:
            if not exc.args or exc.args[0] not in (LOCK_WAIT_TIMEOUT, DEADLOCK):
                raise
            log.warning("%s: MySQL error %s, retrying once", func.__name__, exc.args[0])
            return func(*args, **kwargs)
    return wrapper


def init_db(app: Flask) -> None:
    app.teardown_appcontext(close_db)
