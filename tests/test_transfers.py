"""Deposits, withdrawals, and moves between own accounts (task 3.1; R11, R12, R19, A5, D15;
properties P1, P2, P4, P9; N2.5)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.crypto import encrypt_field, mask_last4
from app.errors import InsufficientFunds, NoBankAccount, NotFound, ValidationFailed
from app.money import MAX_MONEY
from app.services import bank_accounts, transfers
from tests.factories import make_bank_account, make_portfolio, make_user

pytestmark = pytest.mark.db

D = Decimal
AZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=AZ)  # a Monday morning in Arizona
TODAY = NOW.date()


def one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    db.commit()
    return found


def cash(db, portfolio_id):
    return one(db, "SELECT cash_balance FROM Portfolio WHERE portfolioID = %s", (portfolio_id,))["cash_balance"]


def counts(db):
    return tuple(one(db, f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("Transfer", "History"))


@pytest.fixture
def acct(db, app_ctx, fixed_clock):
    """A customer with one bank account and one portfolio holding $500.00."""
    fixed_clock(NOW)
    user = make_user(db)
    return type("Acct", (), {
        "user": user,
        "bank": encrypted_bank(db, user, "000111112222"),
        "portfolio": make_portfolio(db, user, cash_balance=D("500.00")),
    })


# --- Deposit (R11) ------------------------------------------------------------------------------

def test_deposit_adds_cash_and_records_everything(db, acct):  # R11.4, SR11
    result = transfers.deposit(acct.user, acct.bank, acct.portfolio, D("250.25"), TODAY)
    assert result.cash_balance == D("750.25") == cash(db, acct.portfolio)  # R11.5
    t = one(db, "SELECT * FROM Transfer WHERE transferID = %s", (result.transfer_id,))
    assert (t["transfer_type"], t["status"], t["amount"]) == ("Deposit", "Completed", D("250.25"))
    assert (t["to_portfolioID"], t["from_portfolioID"], t["bankAccountID"]) == (acct.portfolio, None, acct.bank)
    assert t["transfer_date"] == TODAY
    assert t["created_at"] == NOW.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)  # UTC, millisecond column
    h = one(db, "SELECT * FROM History WHERE transferID = %s", (result.transfer_id,))
    assert (h["portfolioID"], h["action"], h["transactionID"]) == (acct.portfolio, "Deposit", None)  # R17.1


def test_withdrawal_removes_cash_and_records_everything(db, acct):  # R12.4
    result = transfers.withdraw(acct.user, acct.portfolio, acct.bank, D("120.10"), TODAY)
    assert result.cash_balance == D("379.90") == cash(db, acct.portfolio)
    t = one(db, "SELECT * FROM Transfer WHERE transferID = %s", (result.transfer_id,))
    assert (t["transfer_type"], t["status"]) == ("Withdrawal", "Completed")
    assert (t["from_portfolioID"], t["to_portfolioID"]) == (acct.portfolio, None)
    assert one(db, "SELECT action FROM History WHERE transferID = %s", (result.transfer_id,))["action"] == "Withdrawal"


def test_withdrawing_the_whole_balance_is_allowed(db, acct):
    assert transfers.withdraw(acct.user, acct.portfolio, acct.bank, D("500.00"), TODAY).cash_balance == D("0.00")


# --- Rejections change nothing (P9) ----------------------------------------------------------------

def test_withdrawing_more_than_the_balance_is_refused(db, acct):  # R12.3, P2
    with pytest.raises(InsufficientFunds) as exc:
        transfers.withdraw(acct.user, acct.portfolio, acct.bank, D("500.01"), TODAY)
    assert exc.value.message == "Insufficient funds"
    assert cash(db, acct.portfolio) == D("500.00") and counts(db) == (0, 0)


@pytest.mark.parametrize("amount", [D("0"), D("0.00"), D("-5.00"), D("10.005"), D("NaN"), MAX_MONEY + D("0.01"), 10.5])
@pytest.mark.parametrize("operation", ["deposit", "withdraw"])
def test_invalid_amounts_are_refused(db, acct, operation, amount):  # R11.3, R12.2
    with pytest.raises(ValidationFailed) as exc:
        if operation == "deposit":
            transfers.deposit(acct.user, acct.bank, acct.portfolio, amount, TODAY)
        else:
            transfers.withdraw(acct.user, acct.portfolio, acct.bank, amount, TODAY)
    assert exc.value.field == "amount"
    assert cash(db, acct.portfolio) == D("500.00") and counts(db) == (0, 0)


@pytest.mark.parametrize("transfer_date", [TODAY - timedelta(days=1), TODAY + timedelta(days=1), None])
def test_transfer_date_must_be_today(db, acct, transfer_date):  # A5
    with pytest.raises(ValidationFailed) as exc:
        transfers.deposit(acct.user, acct.bank, acct.portfolio, D("10.00"), transfer_date)
    assert exc.value.field == "transfer_date" and "09/28/2026" in exc.value.message
    assert counts(db) == (0, 0)


def test_today_means_the_arizona_date(db, acct, fixed_clock):  # A5, D6
    late_evening = datetime(2026, 9, 28, 23, 30, tzinfo=AZ)  # already 29 September in UTC
    fixed_clock(late_evening)
    transfers.deposit(acct.user, acct.bank, acct.portfolio, D("1.00"), date(2026, 9, 28))
    with pytest.raises(ValidationFailed):
        transfers.deposit(acct.user, acct.bank, acct.portfolio, D("1.00"), date(2026, 9, 29))


def test_deposit_cannot_push_the_balance_past_the_maximum(db, acct):
    one(db, "UPDATE Portfolio SET cash_balance = %s WHERE portfolioID = %s", (MAX_MONEY - D("1.00"), acct.portfolio))
    with pytest.raises(ValidationFailed) as exc:
        transfers.deposit(acct.user, acct.bank, acct.portfolio, D("1.01"), TODAY)
    assert exc.value.field == "amount" and counts(db) == (0, 0)


def test_someone_elses_portfolio_or_bank_account_is_not_found(db, acct):  # R2.5
    other = make_user(db)
    their_bank, their_portfolio = make_bank_account(db, other), make_portfolio(db, other, cash_balance=D("900.00"))
    with pytest.raises(NotFound):
        transfers.deposit(acct.user, acct.bank, their_portfolio, D("10.00"), TODAY)
    with pytest.raises(NotFound):
        transfers.withdraw(acct.user, acct.portfolio, their_bank, D("10.00"), TODAY)
    with pytest.raises(NotFound):
        transfers.withdraw(acct.user, their_portfolio, acct.bank, D("10.00"), TODAY)
    assert cash(db, acct.portfolio) == D("500.00") and cash(db, their_portfolio) == D("900.00")
    assert counts(db) == (0, 0)


def test_customer_with_no_bank_account_gets_the_link_one_message(db, app_ctx, fixed_clock):  # R11.6
    fixed_clock(NOW)
    user = make_user(db)
    portfolio = make_portfolio(db, user)
    with pytest.raises(NoBankAccount) as exc:
        transfers.deposit(user, 999999, portfolio, D("10.00"), TODAY)
    assert exc.value.message == "Link a bank account before transferring funds"


# --- P1, P2, P4: any sequence of transfers keeps the books straight --------------------------------

OPERATIONS = st.lists(
    st.tuples(st.sampled_from(["deposit", "withdraw"]),
              st.decimals(min_value=D("0.01"), max_value=D("2000.00"), places=2)),
    min_size=1, max_size=12,
)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None, max_examples=40)
@given(operations=OPERATIONS)
def test_p1_p2_p4_cash_always_matches_the_transfer_records(db, acct, operations):
    with db.cursor() as cur:  # fresh books for every generated sequence
        cur.execute("DELETE FROM History")
        cur.execute("DELETE FROM Transfer")
        cur.execute("UPDATE Portfolio SET cash_balance = 0.00 WHERE portfolioID = %s", (acct.portfolio,))
    db.commit()
    expected = D("0.00")
    for kind, amount in operations:
        if kind == "deposit":
            transfers.deposit(acct.user, acct.bank, acct.portfolio, amount, TODAY)
            expected += amount
        elif amount <= expected:
            transfers.withdraw(acct.user, acct.portfolio, acct.bank, amount, TODAY)
            expected -= amount
        else:
            with pytest.raises(InsufficientFunds):
                transfers.withdraw(acct.user, acct.portfolio, acct.bank, amount, TODAY)
    balance = cash(db, acct.portfolio)
    ins = one(db, "SELECT COALESCE(SUM(amount), 0) AS s FROM Transfer WHERE to_portfolioID = %s AND status = 'Completed'",
              (acct.portfolio,))["s"]
    outs = one(db, "SELECT COALESCE(SUM(amount), 0) AS s FROM Transfer WHERE from_portfolioID = %s AND status = 'Completed'",
               (acct.portfolio,))["s"]
    assert balance == expected == ins - outs          # P1
    assert balance >= 0                               # P2
    transfers_n, history_n = counts(db)
    assert transfers_n == history_n                   # P4: one History row per transfer
    assert one(db, "SELECT COUNT(DISTINCT transferID) AS n FROM History")["n"] == transfers_n


# --- N2.5: concurrent withdrawals cannot overdraw -----------------------------------------------------

def test_parallel_withdrawals_never_overdraw(db, web_app, acct):  # N2.5, P2
    def withdraw_100(_):
        with web_app.app_context():
            try:
                transfers.withdraw(acct.user, acct.portfolio, acct.bank, D("100.00"), TODAY)
                return "ok"
            except InsufficientFunds:
                return "refused"

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(withdraw_100, range(10)))
    assert results.count("ok") == 5 and results.count("refused") == 5
    assert cash(db, acct.portfolio) == D("0.00")
    assert counts(db) == (5, 5)

# --- Moves between the customer's own brokerage accounts (R19, D15) ---------------------------------

@pytest.fixture
def pair(db, app_ctx, fixed_clock):
    """A customer with no bank account and two portfolios: A holds $500.00, B holds $100.00 (R19.1)."""
    fixed_clock(NOW)
    user = make_user(db)
    return type("Pair", (), {
        "user": user,
        "a": make_portfolio(db, user, account_name="Growth", cash_balance=D("500.00")),
        "b": make_portfolio(db, user, account_name="Income", cash_balance=D("100.00")),
    })


def test_move_between_own_accounts_records_everything(db, pair):  # R19.1, R19.3, R19.4, R19.5
    result = transfers.move_between(pair.user, pair.a, pair.b, D("125.50"), TODAY)
    assert (result.transfer_type, result.amount) == ("Internal", D("125.50"))
    assert (result.portfolio_name, result.cash_balance) == ("Growth", D("374.50")) == ("Growth", cash(db, pair.a))
    assert (result.to_portfolio_name, result.to_cash_balance) == ("Income", D("225.50")) == ("Income", cash(db, pair.b))
    t = one(db, "SELECT * FROM Transfer WHERE transferID = %s", (result.transfer_id,))
    assert (t["transfer_type"], t["status"], t["amount"]) == ("Internal", "Completed", D("125.50"))
    assert (t["from_portfolioID"], t["to_portfolioID"], t["bankAccountID"]) == (pair.a, pair.b, None)
    with db.cursor() as cur:
        cur.execute("SELECT portfolioID, action, `timestamp` FROM History WHERE transferID = %s ORDER BY action DESC",
                    (result.transfer_id,))
        rows = cur.fetchall()
    db.commit()
    assert [(r["portfolioID"], r["action"]) for r in rows] == [(pair.a, "Transfer out"), (pair.b, "Transfer in")]
    assert {r["timestamp"] for r in rows} == {t["created_at"]}  # both timestamped with the move (R17.1)


def test_moving_the_whole_balance_is_allowed(db, pair):
    transfers.move_between(pair.user, pair.a, pair.b, D("500.00"), TODAY)
    assert (cash(db, pair.a), cash(db, pair.b)) == (D("0.00"), D("600.00"))


def test_moving_more_than_the_from_balance_is_refused(db, pair):  # R19.2, P2, P9
    with pytest.raises(InsufficientFunds) as exc:
        transfers.move_between(pair.user, pair.b, pair.a, D("100.01"), TODAY)
    assert exc.value.message == "Insufficient funds"
    assert (cash(db, pair.a), cash(db, pair.b)) == (D("500.00"), D("100.00")) and counts(db) == (0, 0)


def test_moving_to_the_same_account_is_refused(db, pair):  # R19.2
    with pytest.raises(ValidationFailed) as exc:
        transfers.move_between(pair.user, pair.a, pair.a, D("10.00"), TODAY)
    assert exc.value.field == "to_account"
    assert cash(db, pair.a) == D("500.00") and counts(db) == (0, 0)


@pytest.mark.parametrize("amount", [D("0.00"), D("-5.00"), D("10.005"), D("Infinity"), 10.5])
def test_moves_use_the_same_amount_rules(db, pair, amount):  # R19.2, R11.3
    with pytest.raises(ValidationFailed) as exc:
        transfers.move_between(pair.user, pair.a, pair.b, amount, TODAY)
    assert exc.value.field == "amount" and counts(db) == (0, 0)


@pytest.mark.parametrize("transfer_date", [TODAY - timedelta(days=1), TODAY + timedelta(days=1), None])
def test_moves_use_the_same_date_rule(db, pair, transfer_date):  # R19.2, A5
    with pytest.raises(ValidationFailed) as exc:
        transfers.move_between(pair.user, pair.a, pair.b, D("10.00"), transfer_date)
    assert exc.value.field == "transfer_date" and counts(db) == (0, 0)


def test_a_move_cannot_push_the_to_balance_past_the_maximum(db, pair):
    one(db, "UPDATE Portfolio SET cash_balance = %s WHERE portfolioID = %s", (MAX_MONEY, pair.b))
    with pytest.raises(ValidationFailed) as exc:
        transfers.move_between(pair.user, pair.a, pair.b, D("0.01"), TODAY)
    assert exc.value.field == "amount"
    assert (cash(db, pair.a), cash(db, pair.b)) == (D("500.00"), MAX_MONEY) and counts(db) == (0, 0)


def test_moves_involving_someone_elses_account_are_not_found(db, pair):  # R19.6, R2.5
    stranger = make_user(db)
    theirs = make_portfolio(db, stranger, cash_balance=D("900.00"))
    with pytest.raises(NotFound):
        transfers.move_between(pair.user, pair.a, theirs, D("10.00"), TODAY)   # paying into a stranger's account
    with pytest.raises(NotFound):
        transfers.move_between(pair.user, theirs, pair.a, D("10.00"), TODAY)   # taking from it
    with pytest.raises(NotFound):
        transfers.move_between(stranger, pair.a, theirs, D("10.00"), TODAY)    # the stranger taking from ours
    assert (cash(db, pair.a), cash(db, pair.b), cash(db, theirs)) == (D("500.00"), D("100.00"), D("900.00"))
    assert counts(db) == (0, 0)


# P1, P2, P4 across deposits, withdrawals, and moves between two accounts

MIXED = st.lists(
    st.tuples(st.sampled_from(["deposit", "withdraw", "move"]), st.sampled_from([0, 1]),
              st.decimals(min_value=D("0.01"), max_value=D("2000.00"), places=2)),
    min_size=1, max_size=12,
)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None, max_examples=40)
@given(operations=MIXED)
def test_p1_p2_p4_hold_with_moves_between_accounts(db, acct, operations):
    second = one(db, "SELECT portfolioID FROM Portfolio WHERE userID = %s AND portfolioID <> %s", (acct.user, acct.portfolio))
    second = second["portfolioID"] if second else make_portfolio(db, acct.user)
    accounts = [acct.portfolio, second]
    with db.cursor() as cur:  # fresh books for every generated sequence
        cur.execute("DELETE FROM History")
        cur.execute("DELETE FROM Transfer")
        cur.execute("UPDATE Portfolio SET cash_balance = 0.00 WHERE userID = %s", (acct.user,))
    db.commit()
    expected = {p: D("0.00") for p in accounts}
    for kind, index, amount in operations:
        here, there = accounts[index], accounts[1 - index]
        if kind == "deposit":
            transfers.deposit(acct.user, acct.bank, here, amount, TODAY)
            expected[here] += amount
        elif amount > expected[here]:
            with pytest.raises(InsufficientFunds):
                if kind == "withdraw":
                    transfers.withdraw(acct.user, here, acct.bank, amount, TODAY)
                else:
                    transfers.move_between(acct.user, here, there, amount, TODAY)
        elif kind == "withdraw":
            transfers.withdraw(acct.user, here, acct.bank, amount, TODAY)
            expected[here] -= amount
        else:
            transfers.move_between(acct.user, here, there, amount, TODAY)
            expected[here] -= amount
            expected[there] += amount
    for p in accounts:
        ins = one(db, "SELECT COALESCE(SUM(amount), 0) AS s FROM Transfer WHERE to_portfolioID = %s AND status = 'Completed'",
                  (p,))["s"]
        outs = one(db, "SELECT COALESCE(SUM(amount), 0) AS s FROM Transfer WHERE from_portfolioID = %s AND status = 'Completed'",
                   (p,))["s"]
        assert cash(db, p) == expected[p] == ins - outs   # P1, per account
        assert cash(db, p) >= 0                           # P2
    # P4: one History row per deposit or withdrawal, two per move (one on each account, R19.4)
    wrong = one(db, "SELECT COUNT(*) AS n FROM Transfer t"
                    " WHERE (SELECT COUNT(*) FROM History h WHERE h.transferID = t.transferID)"
                    "    <> IF(t.transfer_type = 'Internal', 2, 1)")["n"]
    assert wrong == 0
    assert one(db, "SELECT COUNT(*) AS n FROM History h JOIN Transfer t USING (transferID)"
                   " WHERE h.portfolioID NOT IN (COALESCE(t.from_portfolioID, 0), COALESCE(t.to_portfolioID, 0))")["n"] == 0


# --- Opposite moves at the same time: no deadlock, nothing lost (design: lock in portfolioID order) ---

def test_opposite_moves_at_the_same_time_do_not_deadlock(db, web_app, pair):  # R19.3, P1
    def move(i):
        source, target = (pair.a, pair.b) if i % 2 == 0 else (pair.b, pair.a)
        with web_app.app_context():
            transfers.move_between(pair.user, source, target, D("1.00"), TODAY)
            return "ok"

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(move, range(40)))  # any deadlock or lock timeout would raise here
    assert results.count("ok") == 40
    assert (cash(db, pair.a), cash(db, pair.b)) == (D("500.00"), D("100.00"))  # 20 each way cancel out
    assert counts(db) == (40, 80)


# --- Limits are stated, not just "too large" ---------------------------------------------------------

def test_amount_over_the_limit_states_the_limit(db, acct):
    with pytest.raises(ValidationFailed) as exc:
        transfers.deposit(acct.user, acct.bank, acct.portfolio, MAX_MONEY + D("0.01"), TODAY)
    assert exc.value.message == "The largest amount you can enter is $99,999,999.99."


def test_balance_limit_is_stated(db, acct):
    one(db, "UPDATE Portfolio SET cash_balance = %s WHERE portfolioID = %s", (MAX_MONEY, acct.portfolio))
    with pytest.raises(ValidationFailed) as exc:
        transfers.deposit(acct.user, acct.bank, acct.portfolio, D("0.01"), TODAY)
    assert exc.value.message == "That deposit would take the cash balance over the maximum of $99,999,999.99."


# --- Removed bank accounts (R10.7, D25) --------------------------------------------------------------

def encrypted_bank(db, user, number="000123456789"):
    """Removal returns the masked number, so these need a really encrypted account number."""
    return make_bank_account(db, user, account_number=encrypt_field(number))


def test_a_removed_bank_account_cannot_be_used(db, acct):  # R10.7
    bank = encrypted_bank(db, acct.user)
    other_bank = make_bank_account(db, acct.user)
    bank_accounts.remove_bank_account(acct.user, bank)
    for attempt in (lambda: transfers.deposit(acct.user, bank, acct.portfolio, D("10.00"), TODAY),
                    lambda: transfers.withdraw(acct.user, acct.portfolio, bank, D("10.00"), TODAY)):
        with pytest.raises(NotFound):
            attempt()
    assert transfers.deposit(acct.user, other_bank, acct.portfolio, D("10.00"), TODAY).cash_balance == D("510.00")


def test_removing_the_last_bank_account_brings_back_the_link_message(db, acct):  # R11.6
    bank = encrypted_bank(db, acct.user)
    bank_accounts.remove_bank_account(acct.user, acct.bank)
    assert transfers.has_bank_account(acct.user)
    bank_accounts.remove_bank_account(acct.user, bank)
    assert not transfers.has_bank_account(acct.user)
    with pytest.raises(NoBankAccount):
        transfers.deposit(acct.user, bank, acct.portfolio, D("10.00"), TODAY)


def test_past_transfers_keep_a_removed_bank_account(db, acct):  # R10.7, R17.2
    bank = encrypted_bank(db, acct.user)
    result = transfers.deposit(acct.user, bank, acct.portfolio, D("25.00"), TODAY)
    bank_accounts.remove_bank_account(acct.user, bank)
    t = one(db, "SELECT bankAccountID FROM Transfer WHERE transferID = %s", (result.transfer_id,))
    assert t["bankAccountID"] == bank
    assert one(db, "SELECT COUNT(*) AS n FROM BankAccount WHERE bankAccountID = %s", (bank,))["n"] == 1  # not deleted
    assert one(db, "SELECT COUNT(*) AS n FROM History WHERE transferID = %s", (result.transfer_id,))["n"] == 1


def test_removal_is_limited_to_the_owner_and_happens_once(db, acct):  # R10.8, R2.5
    bank = encrypted_bank(db, acct.user)
    stranger = make_user(db)
    with pytest.raises(NotFound):
        bank_accounts.remove_bank_account(stranger, bank)
    assert [b.bank_account_id for b in bank_accounts.list_bank_accounts(acct.user)] == [acct.bank, bank]
    removed = bank_accounts.remove_bank_account(acct.user, bank)
    assert removed.masked_account_number == mask_last4("000123456789")
    assert [b.bank_account_id for b in bank_accounts.list_bank_accounts(acct.user)] == [acct.bank]
    with pytest.raises(NotFound):
        bank_accounts.remove_bank_account(acct.user, bank)       # already removed
    with pytest.raises(NotFound):
        bank_accounts.get_owned_bank_account(acct.user, bank)
