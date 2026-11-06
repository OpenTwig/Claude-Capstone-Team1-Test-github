"""The History page: every trade and transfer, newest first, 20 to a page.

Customers see the transactions in all of their own portfolios.
Administrators see every customer's transactions.
"""
from datetime import timezone

from flask import Blueprint, render_template, request
from flask_login import current_user

from app.common import ARIZONA, decrypt, make_page, money, shares
from app.models import History, Portfolio, mask

bp = Blueprint("history", __name__)

ROWS_PER_PAGE = 20


def to_arizona_time(stored_time):
    """Turn a time read from the database (UTC) into Arizona time, for showing on a page."""
    utc_time = stored_time.replace(tzinfo=timezone.utc)
    return utc_time.astimezone(ARIZONA)


def describe(history_row):
    """One line of the History table, as a dictionary.

    "amount" is how the cash changed: positive when money came in, negative when it went out.
    """
    portfolio = history_row.portfolio
    line = {
        "at": to_arizona_time(history_row.timestamp),
        "type": history_row.action,
        "symbol": None,
        "quantity": None,
        "price": None,
        "customer": portfolio.owner.name,
        "account": portfolio.account_name + " " + portfolio.masked_account_number,
    }

    trade = history_row.trade
    if trade is not None:
        symbol = trade.issuer.symbol
        how_many = shares(trade.quantity)
        price = money(trade.price_per_share)
        line["symbol"] = symbol
        line["quantity"] = trade.quantity
        line["price"] = trade.price_per_share
        line["status"] = trade.status
        if trade.type == "Buy":
            line["description"] = f"Bought {how_many} {symbol} @ {price}"
            line["amount"] = -trade.total_amount
        else:
            line["description"] = f"Sold {how_many} {symbol} @ {price}"
            line["amount"] = trade.total_amount
        return line

    transfer = history_row.transfer
    line["status"] = transfer.status
    action = history_row.action
    if action == "Deposit" or action == "Withdrawal":
        bank = transfer.bank_account  # still shown even if it was removed later
        bank_name = bank.nickname + " " + mask(decrypt(bank.account_number))
        if action == "Deposit":
            line["description"] = "Deposit from " + bank_name
            line["amount"] = transfer.amount
        else:
            line["description"] = "Withdrawal to " + bank_name
            line["amount"] = -transfer.amount
    elif action == "Transfer out":
        other = transfer.to_portfolio
        line["description"] = "Transfer to " + other.account_name + " " + other.masked_account_number
        line["amount"] = -transfer.amount
    else:  # "Transfer in"
        other = transfer.from_portfolio
        line["description"] = "Transfer from " + other.account_name + " " + other.masked_account_number
        line["amount"] = transfer.amount
    return line


@bp.get("/history")
def history_page():
    query = History.query
    if not current_user.is_admin:
        # Only the rows for this customer's own portfolios.
        query = query.join(Portfolio, Portfolio.portfolio_id == History.portfolio_id)
        query = query.filter(Portfolio.user_id == current_user.user_id)
    query = query.order_by(History.timestamp.desc(), History.history_id.desc())  # newest first

    page = make_page(query, request.args.get("page", 1, type=int), ROWS_PER_PAGE)
    lines = []
    for history_row in page["rows"]:
        lines.append(describe(history_row))

    return render_template("history/history.html", lines=lines, page=page, is_admin=current_user.is_admin)
