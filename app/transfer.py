"""The Transfer Funds page. Customers only. It can:

    deposit     move money from a bank account into a portfolio
    withdraw    move money from a portfolio to a bank account
    move        move money from one of the customer's portfolios to another
    and link or remove bank accounts.

No real money moves. We record each transfer, and change the cash balance, in one commit.
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.auth import customer_required
from app.common import (UserError, check_digits, check_text, decrypt,
                        encrypt, list_portfolios, money, now_for_database, parse_money, today)
from app.models import BankAccount, History, Portfolio, Transfer, db, mask

bp = Blueprint("transfer", __name__)

SUPPORT_EMAIL = "support@example.com"
SUPPORT_HOURS = "Monday–Friday, 9:00 AM–5:00 PM Arizona time"


# --- Looking things up -------------------------------------------------------------------------------

def list_bank_accounts(user_id):
    """The customer's bank accounts. The account number is shown masked, never in full."""
    accounts = (
        BankAccount.query
        .filter_by(user_id=user_id, is_active=True)
        .order_by(BankAccount.created_at, BankAccount.bank_account_id)
        .all()
    )
    result = []
    for account in accounts:
        result.append({
            "bank_account_id": account.bank_account_id,
            "bank_name": account.bank_name,
            "nickname": account.nickname,
            "masked_account_number": mask(decrypt(account.account_number)),
        })
    return result


def menu_labels(names_and_ids, masked_numbers):
    """The names to show in the From and To menus, by id.

    Usually just the name. When two accounts have the same name, both also show their last
    4 digits, so you can tell them apart.
    """
    name_count = {}
    for name, _ in names_and_ids:
        name_count[name] = name_count.get(name, 0) + 1

    labels = {}
    for name, account_id in names_and_ids:
        if name_count[name] == 1:
            labels[account_id] = name
        else:
            labels[account_id] = name + " " + masked_numbers[account_id]
    return labels


def show_transfer_page(errors=None, form_values=None, bank_errors=None, bank_form_values=None):
    banks = list_bank_accounts(current_user.user_id)
    portfolios = list_portfolios(current_user.user_id)

    bank_names = []
    bank_numbers = {}
    for bank in banks:
        bank_names.append((bank["nickname"], bank["bank_account_id"]))
        bank_numbers[bank["bank_account_id"]] = bank["masked_account_number"]

    portfolio_names = []
    portfolio_numbers = {}
    for portfolio in portfolios:
        portfolio_names.append((portfolio.account_name, portfolio.portfolio_id))
        portfolio_numbers[portfolio.portfolio_id] = portfolio.masked_account_number

    return render_template(
        "transfer/transfer.html",
        banks=banks,
        portfolios=portfolios,
        bank_labels=menu_labels(bank_names, bank_numbers),
        portfolio_labels=menu_labels(portfolio_names, portfolio_numbers),
        today=today(),
        errors=errors or {},
        values=form_values or {},
        bank_errors=bank_errors or {},
        bank_values=bank_form_values or {},
        support_email=SUPPORT_EMAIL,
        support_hours=SUPPORT_HOURS,
    )


def lock_portfolio(portfolio_id):
    """One of the customer's portfolios, locked until we commit (404 if it isn't theirs).

    The lock stops two transfers changing the same cash balance at the same moment.
    """
    query = Portfolio.query.filter_by(portfolio_id=portfolio_id, user_id=current_user.user_id)
    return query.with_for_update().first_or_404()


def check_bank_account(bank_account_id):
    """404 unless this is one of the customer's bank accounts and it hasn't been removed."""
    query = BankAccount.query.filter_by(bank_account_id=bank_account_id,
                                        user_id=current_user.user_id, is_active=True)
    query.first_or_404()


def record_transfer(kind, amount, from_portfolio_id, to_portfolio_id, bank_account_id):
    """Add the Transfer row, and a History row for each portfolio it touched."""
    now = now_for_database()
    transfer = Transfer(transfer_type=kind, amount=amount, from_portfolio_id=from_portfolio_id,
                        to_portfolio_id=to_portfolio_id, bank_account_id=bank_account_id,
                        transfer_date=today(), created_at=now)
    db.session.add(transfer)

    if kind == "Deposit":
        db.session.add(History(portfolio_id=to_portfolio_id, transfer=transfer,
                               action="Deposit", timestamp=now))
    elif kind == "Withdrawal":
        db.session.add(History(portfolio_id=from_portfolio_id, transfer=transfer,
                               action="Withdrawal", timestamp=now))
    else:
        db.session.add(History(portfolio_id=from_portfolio_id, transfer=transfer,
                               action="Transfer out", timestamp=now))
        db.session.add(History(portfolio_id=to_portfolio_id, transfer=transfer,
                               action="Transfer in", timestamp=now))


# --- The three kinds of transfer. Each returns the message to show, or raises UserError. -------------

def deposit(bank_account_id, portfolio_id, amount):
    check_bank_account(bank_account_id)
    portfolio = lock_portfolio(portfolio_id)
    portfolio.cash_balance = portfolio.cash_balance + amount
    record_transfer("Deposit", amount, None, portfolio_id, bank_account_id)
    db.session.commit()
    return (f"Deposited {money(amount)} into {portfolio.account_name}. "
            f"New cash balance: {money(portfolio.cash_balance)}.")


def withdraw(portfolio_id, bank_account_id, amount):
    check_bank_account(bank_account_id)
    portfolio = lock_portfolio(portfolio_id)
    if amount > portfolio.cash_balance:
        raise UserError("Insufficient funds")

    portfolio.cash_balance = portfolio.cash_balance - amount
    record_transfer("Withdrawal", amount, portfolio_id, None, bank_account_id)
    db.session.commit()
    return (f"Withdrew {money(amount)} from {portfolio.account_name}. "
            f"New cash balance: {money(portfolio.cash_balance)}.")


def move_between_portfolios(from_id, to_id, amount):
    if from_id == to_id:
        raise UserError("Choose a different account to move the money to.", "to_account")

    # Always lock the lower id first. If two moves went in opposite directions at the same time
    # and each locked "its" portfolio first, they could wait for each other forever (a deadlock).
    if from_id < to_id:
        source = lock_portfolio(from_id)
        target = lock_portfolio(to_id)
    else:
        target = lock_portfolio(to_id)
        source = lock_portfolio(from_id)

    if amount > source.cash_balance:
        raise UserError("Insufficient funds")

    source.cash_balance = source.cash_balance - amount
    target.cash_balance = target.cash_balance + amount
    record_transfer("Internal", amount, from_id, to_id, None)
    db.session.commit()
    return (f"Moved {money(amount)} from {source.account_name} "
            f"(new cash balance {money(source.cash_balance)}) to {target.account_name} "
            f"(new cash balance {money(target.cash_balance)}).")


# --- Pages -------------------------------------------------------------------------------------------

def read_menu_choice(value):
    """The From and To menus send text like "bank:12" or "portfolio:3".

    Returns the kind ("bank" or "portfolio") and the id (12), or (None, None) if nothing valid
    was chosen.
    """
    kind, _, id_text = (value or "").partition(":")
    if kind not in ["bank", "portfolio"] or not id_text.isdigit():
        return None, None
    return kind, int(id_text)


@bp.get("/transfer")
@customer_required
def transfer_page():
    return show_transfer_page()


@bp.post("/transfer")
@customer_required
def submit_transfer():
    form_values = {
        "from_account": request.form.get("from_account", ""),
        "to_account": request.form.get("to_account", ""),
        "amount": request.form.get("amount", ""),
    }
    from_kind, from_id = read_menu_choice(form_values["from_account"])
    to_kind, to_id = read_menu_choice(form_values["to_account"])

    # Check every field first, so all the problems show at once.
    errors = {}
    if from_kind is None:
        errors["from_account"] = "Choose where the money comes from."
    if to_kind is None:
        errors["to_account"] = "Choose where the money goes."
    elif from_kind == "bank" and to_kind == "bank":
        errors["to_account"] = ("Money can't move from one bank account to another. "
                                "Choose a portfolio for From or To.")
    try:
        amount = parse_money(form_values["amount"])
    except UserError as error:
        errors["amount"] = error.message
    if errors:
        return show_transfer_page(errors, form_values)

    try:
        if from_kind == "bank":
            message = deposit(from_id, to_id, amount)
        elif to_kind == "bank":
            message = withdraw(from_id, to_id, amount)
        else:
            message = move_between_portfolios(from_id, to_id, amount)
    except UserError as error:
        db.session.rollback()  # undo any change, and release the locks
        return show_transfer_page({error.field: error.message}, form_values)

    flash(message, "success")
    return redirect(url_for("transfer.transfer_page"))


@bp.post("/transfer/bank-accounts")
@customer_required
def add_bank_account():
    bank_form_values = {
        "bank_name": request.form.get("bank_name", ""),
        "account_number": request.form.get("account_number", ""),
        "routing_number": request.form.get("routing_number", ""),
        "nickname": request.form.get("nickname", ""),
    }
    try:
        bank_name = check_text(bank_form_values["bank_name"], "bank_name", "Bank name")
        account_number = check_digits(bank_form_values["account_number"], "account_number",
                                      r"[0-9]{4,17}", "Account number must be 4 to 17 digits.")
        routing_number = check_digits(bank_form_values["routing_number"], "routing_number",
                                      r"[0-9]{9}", "Routing number must be exactly 9 digits.")
        nickname = check_text(bank_form_values["nickname"], "nickname", "Nickname")
    except UserError as error:
        bank_form_values["account_number"] = ""  # never put an account number back into the page
        return show_transfer_page(bank_errors={error.field: error.message},
                                  bank_form_values=bank_form_values)

    account = BankAccount(user_id=current_user.user_id, bank_name=bank_name, nickname=nickname,
                          account_number=encrypt(account_number), routing_number=routing_number)
    db.session.add(account)
    db.session.commit()
    flash(f'Bank account "{nickname}" ({mask(account_number)}) linked.', "success")
    return redirect(url_for("transfer.transfer_page"))


@bp.post("/transfer/bank-accounts/<int:bank_account_id>/remove")
@customer_required
def remove_bank_account(bank_account_id):
    """Hide a bank account. It isn't deleted, so old transfers can still show it."""
    query = BankAccount.query.filter_by(bank_account_id=bank_account_id,
                                        user_id=current_user.user_id, is_active=True)
    account = query.first_or_404()
    account.is_active = False
    db.session.commit()
    flash("Bank account removed. Past transfers still show it.", "success")
    return redirect(url_for("transfer.transfer_page"))
