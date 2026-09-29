"""The static SQL check (task 5.1; N2.1): the app passes it, and it catches the patterns it must."""
from pathlib import Path
from textwrap import dedent

import pytest

from tests.security.sql_static import check_source, check_tree

APP = Path(__file__).resolve().parents[2] / "app"


def test_no_sql_in_the_app_is_built_from_untrusted_text():  # N2.1
    findings = check_tree(APP)
    assert not findings, "\n" + "\n".join(str(f) for f in findings)


UNSAFE = {
    "f-string with a value": '''
        def find(cur, email):
            cur.execute(f"SELECT * FROM `User` WHERE email = '{email}'")
    ''',
    "% formatting": '''
        def find(cur, email):
            cur.execute("SELECT * FROM `User` WHERE email = '%s'" % email)
    ''',
    ".format()": '''
        def find(cur, email):
            cur.execute("SELECT * FROM `User` WHERE email = '{}'".format(email))
    ''',
    "+ concatenation": '''
        def find(cur, email):
            cur.execute("SELECT * FROM `User` WHERE email = '" + email + "'")
    ''',
    "a variable built from input": '''
        def find(cur, email):
            sql = "SELECT * FROM `User` WHERE email = '" + email + "'"
            cur.execute(sql)
    ''',
    "a variable extended with input": '''
        def find(cur, symbol):
            sql = "SELECT * FROM Issuer WHERE TRUE"
            sql += f" AND ticker_symbol = '{symbol}'"
            cur.execute(sql)
    ''',
    "a helper that returns input": '''
        def _where(symbol):
            return " WHERE ticker_symbol = '" + symbol + "'", []

        def find(cur, symbol):
            where, params = _where(symbol)
            cur.execute("SELECT * FROM Issuer" + where, params)
    ''',
    "a parameter one caller fills with input": '''
        def _count(cur, sql):
            cur.execute("SELECT COUNT(*)" + sql)

        def good(cur):
            _count(cur, " FROM Issuer")

        def bad(cur, table):
            _count(cur, " FROM " + table)
    ''',
    "a parameter nobody in the module passes": '''
        def run(cur, sql):
            cur.execute(sql)
    ''',
    "a request value": '''
        def find(cur, request):
            cur.execute(request.args["q"])
    ''',
    "executemany": '''
        def add(cur, table, rows):
            cur.executemany(f"INSERT INTO {table} VALUES (%s)", rows)
    ''',
    "an exemption without a reason": '''
        def find(cur, email):
            cur.execute(f"SELECT '{email}'")  # sql-safe:
    ''',
}


@pytest.mark.parametrize("name", list(UNSAFE))
def test_the_check_catches_unsafe_sql(name):
    assert check_source(dedent(UNSAFE[name])), name


SAFE = {
    "a literal with parameters": '''
        def find(cur, email):
            cur.execute("SELECT * FROM `User` WHERE email = %s", (email,))
    ''',
    "a constant column list": '''
        _COLUMNS = "userID, name, email"

        def find(cur, email):
            cur.execute(f"SELECT {_COLUMNS} FROM `User` WHERE email = %s", (email,))
    ''',
    "a placeholder list": '''
        def find(cur, ids):
            cur.execute("SELECT * FROM Issuer WHERE issuerID IN (" + ", ".join(["%s"] * len(ids)) + ")", ids)
    ''',
    "helpers, tuple unpacking, and +=": '''
        def _where(portfolio_id):
            sql, params = " WHERE TRUE", []
            if portfolio_id is not None:
                sql += " AND portfolioID = %s"
                params.append(portfolio_id)
            return sql, params

        def _count(cur, sql, params):
            cur.execute("SELECT COUNT(*)" + sql, params)

        def count(cur, portfolio_id):
            sql, params = _where(portfolio_id)
            _count(cur, " FROM History" + sql, params)
    ''',
    "a reviewed exemption": '''
        def set_level(cur, level):
            cur.execute(f"SET TRANSACTION ISOLATION LEVEL {level}")  # sql-safe: checked against a fixed list
    ''',
}


@pytest.mark.parametrize("name", list(SAFE))
def test_the_check_accepts_safe_sql(name):
    assert check_source(dedent(SAFE[name])) == [], name
