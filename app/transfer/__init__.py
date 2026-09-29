"""Transfer Funds page (wf-7): deposits, withdrawals, moves between the customer's own
portfolios, and linking bank accounts (R10-R12, R19)."""
from __future__ import annotations

from collections import Counter
from datetime import date

from flask import Blueprint, Flask, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import clock
from app.auth import customer_required
from app.errors import DomainError, NotFound, ValidationFailed
from app.filters import money
from app.money import parse_money
from app.services import bank_accounts, portfolios, transfers
from app.services.market import market_tz

bp = Blueprint("transfer", __name__)

BANK_TO_BANK = "Money can't move from one bank account to another. Choose a portfolio for From or To."


def _labels(accounts, name_of, id_of) -> dict[int, str]:
    """Each account's name, with its last 4 digits added only when another account has the same name."""
    counts = Counter(name_of(a) for a in accounts)
    return {id_of(a): name_of(a) if counts[name_of(a)] == 1 else f"{name_of(a)} {a.masked_account_number}"
            for a in accounts}


def portfolio_labels(user_id: int) -> dict[int, str]:
    return _labels(portfolios.list_portfolios(user_id), lambda p: p.account_name, lambda p: p.portfolio_id)


def _page(errors: dict | None = None, values: dict | None = None, bank_errors: dict | None = None,
          bank_values: dict | None = None, status: int = 200):
    user_id = current_user.user_id
    settings = current_app.config["SETTINGS"]
    banks = bank_accounts.list_bank_accounts(user_id)
    owned = portfolios.list_portfolios(user_id)
    return render_template(
        "transfer/transfer.html",
        banks=banks,
        portfolios=owned,
        bank_labels=_labels(banks, lambda b: b.nickname, lambda b: b.bank_account_id),
        portfolio_labels=_labels(owned, lambda p: p.account_name, lambda p: p.portfolio_id),
        today=clock.now_utc().astimezone(market_tz()).date(),
        errors=errors or {},
        values=values or {},
        bank_errors=bank_errors or {},
        bank_values=bank_values or {},
        support_email=settings.support_email,  # D24
        support_hours=settings.support_hours,
    ), status


def _parse_choice(value: str | None) -> tuple[str, int] | None:
    """'bank:12' -> ('bank', 12); anything else -> None."""
    kind, _, raw_id = (value or "").partition(":")
    if kind in ("bank", "portfolio") and raw_id.isdigit():
        return kind, int(raw_id)
    return None


@bp.get("/transfer")
@customer_required
def transfer_page():
    return _page()


@bp.post("/transfer")
@customer_required
def submit_transfer():
    values = {k: request.form.get(k, "") for k in ("from_account", "to_account", "amount", "transfer_date")}
    errors: dict[str, str] = {}

    source, target = _parse_choice(values["from_account"]), _parse_choice(values["to_account"])
    if source is None:
        errors["from_account"] = "Choose where the money comes from."
    if target is None:
        errors["to_account"] = "Choose where the money goes."
    if source and target and source[0] == target[0] == "bank":
        errors["to_account"] = BANK_TO_BANK
    try:
        amount = parse_money(values["amount"])
    except ValueError as exc:
        errors["amount"] = str(exc)
    try:
        transfer_date = date.fromisoformat(values["transfer_date"].strip())
    except ValueError:
        errors["transfer_date"] = "Enter the transfer date."
    if errors:
        return _page(errors, values)

    try:
        if source[0] == "bank":
            result = transfers.deposit(current_user.user_id, source[1], target[1], amount, transfer_date)
            flash(f"Deposited {money(result.amount)} into {result.portfolio_name}. "
                  f"New cash balance: {money(result.cash_balance)}.", "success")  # R11.5
        elif target[0] == "bank":
            result = transfers.withdraw(current_user.user_id, source[1], target[1], amount, transfer_date)
            flash(f"Withdrew {money(result.amount)} from {result.portfolio_name}. "
                  f"New cash balance: {money(result.cash_balance)}.", "success")  # R12.5
        else:
            result = transfers.move_between(current_user.user_id, source[1], target[1], amount, transfer_date)
            names = portfolio_labels(current_user.user_id)  # adds the last 4 digits only where names repeat
            flash(f"Moved {money(result.amount)} from {names[source[1]]} (new cash balance "
                  f"{money(result.cash_balance)}) to {names[target[1]]} (new cash balance "
                  f"{money(result.to_cash_balance)}).", "success")  # R19.5
    except NotFound:
        raise  # someone else's account or portfolio: the 404 page (R2.5)
    except ValidationFailed as exc:
        return _page({exc.field: exc.message}, values)
    except DomainError as exc:
        return _page({"form": exc.message}, values)  # Insufficient funds, no bank account
    return redirect(url_for("transfer.transfer_page"))


@bp.post("/transfer/bank-accounts")
@customer_required
def add_bank_account():
    fields = ("bank_name", "account_number", "routing_number", "nickname")
    bank_values = {k: request.form.get(k, "") for k in fields}
    try:
        account = bank_accounts.add_bank_account(current_user.user_id, **bank_values)
    except ValidationFailed as exc:
        bank_values["account_number"] = ""  # never echo an account number back into the page
        return _page(bank_errors={exc.field: exc.message}, bank_values=bank_values)
    flash(f"Bank account \"{account.nickname}\" ({account.masked_account_number}) linked.", "success")  # R10.3
    return redirect(url_for("transfer.transfer_page"))


@bp.post("/transfer/bank-accounts/<int:bank_account_id>/remove")
@customer_required
def remove_bank_account(bank_account_id: int):
    """Remove a linked bank account (R10.6-R10.8, D25). Someone else's account is a 404."""
    account = bank_accounts.remove_bank_account(current_user.user_id, bank_account_id)
    flash(f"Bank account \"{account.nickname}\" ({account.masked_account_number}) removed. "
          "Past transfers still show it.", "success")  # R10.7
    return redirect(url_for("transfer.transfer_page"))


def init_transfer(app: Flask) -> None:
    app.register_blueprint(bp)
