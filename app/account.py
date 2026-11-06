"""The Account page (a summary of every portfolio, and Create Portfolio) and the Positions page
(the stocks in one portfolio). Customers only.

How the numbers are worked out:

    Market value   shares x current price
    Total value    cash + the market value of every stock
    Day change     shares x (current price - yesterday's closing price)
    Cost basis     shares x the average price paid
    Gain/loss      market value - cost basis
    YTD change     value now - value on 1 January - money put in since then
                   (so a deposit doesn't count as a gain)
"""
import secrets
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from app.auth import customer_required
from app.common import (UserError, check_text, get_portfolio, list_portfolios, percent, to_cents,
                        today)
from app.models import Inventory, Issuer, Portfolio, PortfolioValueDaily, Transfer, db, mask

bp = Blueprint("account", __name__)

ACCOUNT_TYPES = ["Brokerage"]
ZERO = Decimal("0.00")


# --- Working out the numbers -------------------------------------------------------------------------

def get_positions(portfolio):
    """Every stock in the portfolio, as a list of dictionaries with its numbers worked out."""
    holdings = (
        Inventory.query
        .filter_by(portfolio_id=portfolio.portfolio_id)
        .join(Issuer)
        .order_by(Issuer.symbol)
        .all()
    )
    positions = []
    for holding in holdings:
        stock = holding.issuer
        price = stock.current_price
        previous_close = stock.previous_close(today())
        price_change = price - previous_close

        market_value = to_cents(holding.quantity * price)
        cost_basis = to_cents(holding.quantity * holding.average_cost)

        positions.append({
            "issuer_id": stock.issuer_id,
            "symbol": stock.symbol,
            "company_name": stock.company_name,
            "is_listed": stock.is_listed,
            "quantity": holding.quantity,
            "price": price,
            "price_change": price_change,
            "price_change_pct": percent(price_change, previous_close),
            "market_value": market_value,
            "day_change": to_cents(holding.quantity * price_change),
            "cost_basis": cost_basis,
            "gain_loss": market_value - cost_basis,
            "reinvest": holding.reinvest,
        })
    return positions


def money_put_in_since(portfolio_id, start_date):
    """Money moved into the portfolio since `start_date`, minus money moved out."""
    money_in = (
        db.session.query(func.sum(Transfer.amount))
        .filter(Transfer.to_portfolio_id == portfolio_id)
        .filter(Transfer.transfer_date >= start_date)
        .scalar()
    )
    money_out = (
        db.session.query(func.sum(Transfer.amount))
        .filter(Transfer.from_portfolio_id == portfolio_id)
        .filter(Transfer.transfer_date >= start_date)
        .scalar()
    )
    # sum() gives None when there are no transfers, so count that as 0.
    if money_in is None:
        money_in = ZERO
    if money_out is None:
        money_out = ZERO
    return money_in - money_out


def value_at_start_of_year(portfolio_id):
    """What the portfolio was worth at the last close before 1 January (0 if we don't know)."""
    first_of_january = date(today().year, 1, 1)
    snapshot = (
        PortfolioValueDaily.query
        .filter(PortfolioValueDaily.portfolio_id == portfolio_id)
        .filter(PortfolioValueDaily.value_date < first_of_january)
        .order_by(PortfolioValueDaily.value_date.desc())
        .first()
    )
    if snapshot is None:
        return ZERO
    return snapshot.total_value


def add_up(cash, positions, start_of_year_value, money_put_in):
    """The totals shown at the top of the Account and Positions pages."""
    positions_value = ZERO
    day_change = ZERO
    cost_basis = ZERO
    for position in positions:
        positions_value += position["market_value"]
        day_change += position["day_change"]
        cost_basis += position["cost_basis"]

    total_value = cash + positions_value
    value_yesterday = total_value - day_change
    ytd_change = total_value - start_of_year_value - money_put_in
    gain_loss = positions_value - cost_basis

    return {
        "cash": cash,
        "positions_value": positions_value,
        "total_value": total_value,
        "day_change": day_change,
        "day_change_pct": percent(day_change, value_yesterday),
        "ytd_change": ytd_change,
        "ytd_change_pct": percent(ytd_change, start_of_year_value + money_put_in),
        "cost_basis": cost_basis,
        "gain_loss": gain_loss,
        "gain_loss_pct": percent(gain_loss, cost_basis),
    }


def portfolio_summary(portfolio):
    """One portfolio with its positions and totals."""
    positions = get_positions(portfolio)
    start_of_year_value = value_at_start_of_year(portfolio.portfolio_id)
    money_put_in = money_put_in_since(portfolio.portfolio_id, date(today().year, 1, 1))
    return {
        "portfolio": portfolio,
        "positions": positions,
        "start_of_year_value": start_of_year_value,
        "money_put_in": money_put_in,
        "totals": add_up(portfolio.cash_balance, positions, start_of_year_value, money_put_in),
    }


def chart_data(user_id, portfolio_id, value_now):
    """The points for the value-over-time chart: each day's closing value over the last year
    (saved by the price generator), then today's value.

    With portfolio_id=None, it adds up all of the customer's portfolios.
    """
    one_year_ago = today() - timedelta(days=365)
    query = (
        db.session.query(PortfolioValueDaily.value_date, func.sum(PortfolioValueDaily.total_value))
        .join(Portfolio, Portfolio.portfolio_id == PortfolioValueDaily.portfolio_id)
        .filter(Portfolio.user_id == user_id)
        .filter(PortfolioValueDaily.value_date > one_year_ago)
        .filter(PortfolioValueDaily.value_date < today())
    )
    if portfolio_id is not None:
        query = query.filter(PortfolioValueDaily.portfolio_id == portfolio_id)
    rows = query.group_by(PortfolioValueDaily.value_date).order_by(PortfolioValueDaily.value_date).all()

    labels = []
    values = []
    for day, total in rows:
        labels.append(day.isoformat())
        values.append(str(total))
    labels.append(today().isoformat())
    values.append(str(value_now))
    return {"labels": labels, "values": values}


# --- Pages -------------------------------------------------------------------------------------------

def show_account_page(form_values=None, form_errors=None):
    """The Account page. After a Create Portfolio error, it shows the form again with the error."""
    if form_values is None:
        form_values = {}
    if form_errors is None:
        form_errors = {}

    summaries = []
    all_positions = []
    total_cash = ZERO
    total_start_value = ZERO
    total_money_put_in = ZERO
    for portfolio in list_portfolios(current_user.user_id):
        summary = portfolio_summary(portfolio)
        summaries.append(summary)
        all_positions.extend(summary["positions"])
        total_cash += portfolio.cash_balance
        total_start_value += summary["start_of_year_value"]
        total_money_put_in += summary["money_put_in"]
    totals = add_up(total_cash, all_positions, total_start_value, total_money_put_in)

    # The search box above the Portfolios table (it runs when you press Enter).
    search = request.args.get("q", "").strip()
    shown = []
    for summary in summaries:
        p = summary["portfolio"]
        searchable = f"{p.account_name} {p.account_type} {p.masked_account_number}".lower()
        if search.lower() in searchable:
            shown.append(summary)

    return render_template(
        "account/summary.html",
        summary={"rows": summaries, "totals": totals},
        rows=shown,
        query=search,
        account_types=ACCOUNT_TYPES,
        form_values=form_values,
        form_errors=form_errors,
        open_form=len(form_errors) > 0 or len(summaries) == 0,
        chart=chart_data(current_user.user_id, None, totals["total_value"]),
    )


@bp.get("/account")
@customer_required
def summary():
    return show_account_page()


@bp.post("/account/portfolios")
@customer_required
def create_portfolio():
    form_values = {
        "account_name": request.form.get("account_name", ""),
        "account_type": request.form.get("account_type", ""),
    }
    try:
        name = check_text(form_values["account_name"], "account_name", "Account name")
    except UserError as error:
        return show_account_page(form_values, {error.field: error.message})

    account_number = str(secrets.randbelow(10**10)).zfill(10)  # a random 10-digit number
    portfolio = Portfolio(user_id=current_user.user_id, account_name=name,
                          account_type=ACCOUNT_TYPES[0],  # "Brokerage", the only type
                          account_number=account_number, cash_balance=ZERO)
    db.session.add(portfolio)
    db.session.commit()

    flash(f'Portfolio "{name}" created. Account number {mask(account_number)}.', "success")
    return redirect(url_for("account.summary"))


@bp.get("/positions")
@customer_required
def positions_page():
    portfolios = list_portfolios(current_user.user_id)
    if len(portfolios) == 0:
        return render_template("account/positions.html", view=None, portfolios=portfolios)

    chosen_id = request.args.get("portfolio", "")
    if chosen_id == "":
        portfolio = portfolios[0]
    elif chosen_id.isdigit():
        portfolio = get_portfolio(current_user.user_id, int(chosen_id))  # 404 if it isn't theirs
    else:
        abort(404)

    summary = portfolio_summary(portfolio)
    view = {
        "portfolio": portfolio,
        "positions": summary["positions"],
        "totals": summary["totals"],
        # The "Total Equities" row: just the stocks, no cash.
        "equities": add_up(ZERO, summary["positions"], ZERO, ZERO),
    }
    chart = chart_data(current_user.user_id, portfolio.portfolio_id, summary["totals"]["total_value"])
    return render_template("account/positions.html", view=view, portfolios=portfolios, chart=chart)


@bp.post("/positions/<int:portfolio_id>/<int:issuer_id>/reinvest")
@customer_required
def set_reinvest(portfolio_id, issuer_id):
    """Save the Reinvest? choice for one stock. There are no dividends, so it changes nothing else."""
    get_portfolio(current_user.user_id, portfolio_id)  # 404 if it isn't theirs
    holding = Inventory.query.filter_by(portfolio_id=portfolio_id, issuer_id=issuer_id).first_or_404()
    holding.reinvest = request.form.get("reinvest") == "yes"
    db.session.commit()
    return redirect(url_for("account.positions_page", portfolio=portfolio_id))
