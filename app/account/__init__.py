"""Customer account pages: Summary with Create Portfolio (wf-3, R4, R5) and Positions with the
Reinvest? toggle (wf-4, R6, D22). Customers only (D19)."""
from __future__ import annotations

from flask import Blueprint, Flask, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.auth import customer_required
from app.errors import ValidationFailed
from app.services import portfolios, valuation

bp = Blueprint("account", __name__)


def _chart_data(points) -> dict:
    """Numbers for account.js as a JSON data block (no inline script, CSP)."""
    return {"labels": [d.isoformat() for d, _ in points], "values": [str(v) for _, v in points]}


def _summary_page(form_values: dict | None = None, form_errors: dict | None = None, status: int = 200):
    summary = valuation.account_summary(current_user.user_id)
    query = request.args.get("q", "").strip()
    rows = summary.rows
    if query:  # R5.5: without JavaScript the search box filters here; with it, account.js filters as you type
        needle = query.lower()
        rows = tuple(r for r in rows if needle in f"{r.portfolio.account_name} {r.portfolio.account_type} "
                                                   f"{r.portfolio.masked_account_number}".lower())
    return render_template(
        "account/summary.html", summary=summary, rows=rows, query=query,
        account_types=current_app.config["ACCOUNT_TYPES"],
        form_values=form_values or {}, form_errors=form_errors or {},
        open_form=bool(form_errors) or not summary.rows,
        chart=_chart_data(summary.chart),
    ), status


@bp.get("/account")
@customer_required
def summary():
    return _summary_page()


@bp.post("/account/portfolios")
@customer_required
def create_portfolio():
    values = {k: request.form.get(k, "") for k in ("account_name", "account_type")}
    try:
        created = portfolios.create_portfolio(current_user.user_id, values["account_name"], values["account_type"])
    except ValidationFailed as exc:  # R4.3
        return _summary_page(values, {exc.field: exc.message})
    flash(f'Portfolio "{created.account_name}" created. Account number {created.masked_account_number}.',
          "success")  # R4.4
    return redirect(url_for("account.summary"))


@bp.get("/positions")
@customer_required
def positions_page():
    owned = portfolios.list_portfolios(current_user.user_id)
    if not owned:
        return render_template("account/positions.html", view=None, portfolios=owned)
    raw = request.args.get("portfolio", "")
    if raw and not raw.isdigit():
        abort(404)
    portfolio_id = int(raw) if raw else owned[0].portfolio_id
    view = valuation.positions(current_user.user_id, portfolio_id)  # NotFound unless it is theirs (R2.5)
    return render_template("account/positions.html", view=view, portfolios=owned, chart=_chart_data(view.chart))


@bp.post("/positions/<int:portfolio_id>/<int:issuer_id>/reinvest")
@customer_required
def set_reinvest(portfolio_id: int, issuer_id: int):
    choice = request.form.get("reinvest", "")
    if choice not in ("yes", "no"):
        abort(400)
    portfolios.set_reinvest(current_user.user_id, portfolio_id, issuer_id, choice == "yes")  # R6.7, D22
    return redirect(url_for("account.positions_page", portfolio=portfolio_id))


def init_account(app: Flask) -> None:
    app.register_blueprint(bp)
