"""Portfolios and bank accounts (task 2.2; R4, R10, P13, P14, R2.5)."""
import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pymysql
import pytest

from app.crypto import decrypt_field
from app.errors import NotFound, ValidationFailed
from app.services import bank_accounts, portfolios
from app.services.types import BankAccountRow
from tests.factories import make_portfolio, make_user

pytestmark = pytest.mark.db


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


@pytest.fixture
def owner(db, app_ctx):
    return make_user(db, name="Owner")


@pytest.fixture
def stranger(db, app_ctx):
    return make_user(db, name="Stranger")


# --- Portfolios ---------------------------------------------------------------------------

def test_new_portfolio_starts_with_zero_cash_and_a_ten_digit_number(owner):  # R4.4, A10
    p = portfolios.create_portfolio(owner, "  Retirement  ", "Brokerage")
    assert p.account_name == "Retirement" and p.account_type == "Brokerage"  # D14
    assert p.cash_balance == Decimal("0.00") and isinstance(p.cash_balance, Decimal)
    assert len(p.account_number) == 10 and p.account_number.isdigit()
    assert p.masked_account_number == f"•••• {p.account_number[-4:]}"
    assert p.created_at is not None


def test_new_portfolio_appears_in_the_owners_list_only(owner, stranger):  # R4.5, R2.5
    first = portfolios.create_portfolio(owner, "First", "Brokerage")
    second = portfolios.create_portfolio(owner, "Second", "Brokerage")
    portfolios.create_portfolio(stranger, "Not yours", "Brokerage")
    assert [p.portfolio_id for p in portfolios.list_portfolios(owner)] == [first.portfolio_id, second.portfolio_id]


@pytest.mark.parametrize("field, name, account_type", [
    ("account_name", "", "Brokerage"),
    ("account_name", "   ", "Brokerage"),
    ("account_name", "x" * 101, "Brokerage"),
    ("account_type", "Savings", "Pension"),
    ("account_type", "Savings", ""),
])
def test_invalid_portfolios_are_rejected_and_nothing_is_created(db, owner, field, name, account_type):  # R4.3
    with pytest.raises(ValidationFailed) as exc:
        portfolios.create_portfolio(owner, name, account_type)
    assert exc.value.field == field
    assert one(db, "SELECT COUNT(*) AS n FROM Portfolio")["n"] == 0


def test_the_only_account_type_is_brokerage(app_ctx):  # D14
    from app.config import ACCOUNT_TYPES

    assert ACCOUNT_TYPES == ("Brokerage",) and app_ctx.config["ACCOUNT_TYPES"] == ("Brokerage",)


def test_no_portfolios_if_no_account_types_are_configured(app_ctx, owner):
    app_ctx.config["ACCOUNT_TYPES"] = ()
    with pytest.raises(ValidationFailed, match="have not been set up yet"):
        portfolios.create_portfolio(owner, "Savings", "Brokerage")


def test_p13_account_numbers_are_unique(owner):  # P13
    numbers = [portfolios.create_portfolio(owner, f"P{i}", "Brokerage").account_number for i in range(40)]
    assert len(set(numbers)) == 40


def test_p13_a_colliding_account_number_is_retried(db, owner, monkeypatch):  # P13
    make_portfolio(db, owner, account_number="1111111111")
    candidates = iter(["1111111111", "1111111111", "2222222222"])
    monkeypatch.setattr(portfolios, "generate_account_number", lambda: next(candidates))
    assert portfolios.create_portfolio(owner, "Retried", "Brokerage").account_number == "2222222222"


def test_p13_gives_up_after_five_collisions_without_inserting(db, owner, monkeypatch):
    make_portfolio(db, owner, account_number="1111111111")
    monkeypatch.setattr(portfolios, "generate_account_number", lambda: "1111111111")
    with pytest.raises(pymysql.err.IntegrityError):
        portfolios.create_portfolio(owner, "Never", "Brokerage")
    assert one(db, "SELECT COUNT(*) AS n FROM Portfolio")["n"] == 1


def test_p13_concurrent_creation_gives_unique_numbers(web_app, owner):  # P13
    def create_five(worker):
        with web_app.app_context():
            return [portfolios.create_portfolio(owner, f"W{worker}-{i}", "Brokerage").account_number for i in range(5)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = [n for batch in pool.map(create_five, range(8)) for n in batch]
    assert len(numbers) == 40 and len(set(numbers)) == 40


def test_someone_elses_portfolio_is_not_found(owner, stranger):  # R2.5
    theirs = portfolios.create_portfolio(stranger, "Theirs", "Brokerage")
    with pytest.raises(NotFound):
        portfolios.get_owned_portfolio(owner, theirs.portfolio_id)
    with pytest.raises(NotFound):
        portfolios.get_owned_portfolio(owner, 999999)


# --- Bank accounts ------------------------------------------------------------------------------

def test_bank_account_is_linked_and_shown_masked(db, owner):  # R10.3, R10.5
    account = bank_accounts.add_bank_account(owner, " First Bank ", "000123456789", "021000021", " Checking ")
    assert (account.bank_name, account.nickname, account.routing_number) == ("First Bank", "Checking", "021000021")
    assert account.masked_account_number == "•••• 6789"
    assert "000123456789" not in repr(account)


def test_bank_account_number_is_encrypted_at_rest(db, owner):  # R10.5, N1.3
    account = bank_accounts.add_bank_account(owner, "Bank", "000123456789", "021000021", "Checking")
    stored = one(db, "SELECT account_number FROM BankAccount WHERE bankAccountID = %s", (account.bank_account_id,))
    assert "123456789" not in stored["account_number"] and "6789" not in stored["account_number"]
    assert decrypt_field(stored["account_number"]) == "000123456789"


def test_read_model_never_has_a_full_account_number_field():  # R10.5
    assert {f.name for f in dataclasses.fields(BankAccountRow)} >= {"masked_account_number"}
    assert "account_number" not in {f.name for f in dataclasses.fields(BankAccountRow)}


def test_bank_accounts_are_listed_for_their_owner_only(owner, stranger):  # R10.4, R2.5
    mine = bank_accounts.add_bank_account(owner, "Bank", "11112222", "021000021", "Mine")
    bank_accounts.add_bank_account(stranger, "Bank", "33334444", "021000021", "Theirs")
    listed = bank_accounts.list_bank_accounts(owner)
    assert [a.bank_account_id for a in listed] == [mine.bank_account_id]
    assert listed[0].masked_account_number == "•••• 2222"


@pytest.mark.parametrize("field, overrides", [
    ("bank_name", {"bank_name": ""}),
    ("bank_name", {"bank_name": "x" * 101}),
    ("nickname", {"nickname": "  "}),
    ("nickname", {"nickname": "x" * 101}),
    ("routing_number", {"routing_number": "12345678"}),
    ("routing_number", {"routing_number": "1234567890"}),
    ("routing_number", {"routing_number": "12345678a"}),
    ("routing_number", {"routing_number": "٣٣٣٣٣٣٣٣٣"}),
    ("account_number", {"account_number": "123"}),
    ("account_number", {"account_number": "123456789012345678"}),
    ("account_number", {"account_number": "12-34"}),
])
def test_invalid_bank_accounts_are_rejected_and_nothing_is_saved(db, owner, field, overrides):  # R10.2
    values = {"bank_name": "Bank", "account_number": "12345678", "routing_number": "021000021", "nickname": "Main"}
    values.update(overrides)
    with pytest.raises(ValidationFailed) as exc:
        bank_accounts.add_bank_account(owner, **values)
    assert exc.value.field == field
    assert one(db, "SELECT COUNT(*) AS n FROM BankAccount")["n"] == 0


def test_someone_elses_bank_account_is_not_found(owner, stranger):  # R2.5
    theirs = bank_accounts.add_bank_account(stranger, "Bank", "33334444", "021000021", "Theirs")
    with pytest.raises(NotFound):
        bank_accounts.get_owned_bank_account(owner, theirs.bank_account_id)
