"""Administration page (wf-6): users, stocks, and market hours (R3, R14, R15, R18.9-R18.10, D19-D21, D29).

Administrators only; everyone else gets 403 (R2.4, D19). Every form re-renders the page with the
error next to its field. A temporary password (a new user's, or one issued from the Users list)
is shown once, in the page that answers the POST: it never goes into the session cookie or a
flash message, and that response is marked `Cache-Control: no-store` (R3.4, R3.5).
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, Flask, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user

from app.auth import admin_required
from app.errors import DomainError, ValidationFailed
from app.money import parse_money, parse_shares
from app.services import market, stocks, users, validation, valuation
from app.services.paging import page_number, page_numbers
from app.services.stocks import DESCRIPTION_MAX
from app.trade import market_text

bp = Blueprint("admin", __name__)

USER_FIELDS = ("name", "ssn", "email", "date_of_birth", "role", "access_level", "department")
STOCK_FIELDS = ("company_name", "symbol", "price", "shares", "exchange", "description")
DAY_VALUES = ("0", "1", "2", "3", "4", "5", "6")  # Trading Days checkbox values: date.weekday() numbers (D29)


def _pages(source) -> dict:
    """Both lists' page numbers, so an action on one list comes back to the pages that were showing."""
    return {"users_page": page_number(source.get("users_page")), "stocks_page": page_number(source.get("stocks_page"))}


def _back(anchor: str):
    return redirect(url_for("admin.index", **_pages(request.form), _anchor=anchor))


def _page(*, forms: dict | None = None, errors: dict | None = None, notice: dict | None = None,
          editing: str | None = None, status: int = 200, users_page: int = 1, stocks_page: int = 1):
    """The Administration page.

    forms/errors are keyed by form ("add_user", "add_stock", "schedule", "user-<id>", "stock-<id>");
    `notice` is a one-time temporary password to show; `editing` keeps that edit form open after an error.
    """
    schedule = market.current_schedule()
    now = market.current_status()
    schedule_form = (forms or {}).get("schedule")  # after an error, show what was submitted
    if schedule_form:
        chosen_days = {int(v) for v in schedule_form["trading_days"] if v in DAY_VALUES}
        force_on = schedule_form["force_open"] == "on"
    else:
        chosen_days, force_on = schedule.trading_days, schedule.force_open
    user_page = users.list_users_page(users_page)
    stock_page = valuation.admin_stocks(stocks_page)
    html = render_template(
        "admin/index.html",
        user_page=user_page,
        user_page_numbers=page_numbers(user_page.page, user_page.pages),
        stock_page=stock_page,
        stock_page_numbers=page_numbers(stock_page.page, stock_page.pages),
        schedule=schedule, weekday_names=market.WEEKDAY_NAMES, chosen_days=chosen_days, force_on=force_on,
        days_text=market.days_text, days_short=market.days_short,
        market_open=now.is_open, market_state=_market_state(now),
        forms=forms or {}, errors=errors or {}, notice=notice, editing=editing,
        roles=users.ROLES, description_max=DESCRIPTION_MAX,
        temp_days=current_app.config["SETTINGS"].temp_password_days,
    )
    response = make_response(html, status)
    if notice:
        response.headers["Cache-Control"] = "no-store"  # R3.5: the password must not be kept by the browser
    return response


def _dob(text: str) -> datetime:
    return validation.us_date(text, "date_of_birth", "the date of birth")


def _money(text: str, field: str):
    try:
        return parse_money(text)
    except ValueError as exc:
        raise ValidationFailed(field, str(exc)) from exc


def _shares(text: str, field: str):
    try:
        return parse_shares(text)
    except ValueError as exc:
        raise ValidationFailed(field, str(exc)) from exc


def _time(text: str, field: str, label: str):
    text = (text or "").strip()
    if not text:
        return None  # save_schedule reports the missing time (R15.5)
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError as exc:
        raise ValidationFailed(field, f"Enter the {label} as HH:MM.") from exc


def _days(values: list[str]) -> set[int]:
    """The checked Trading Days boxes, as weekday numbers (0 = Monday). Anything else was tampered with."""
    if not all(v in DAY_VALUES for v in values):
        raise ValidationFailed("trading_days", "Choose trading days from Monday to Sunday.")
    return {int(v) for v in values}  # save_schedule reports an empty choice (R15.5)


def _market_state(status) -> str:
    """The Market Time card's status line (R15.9)."""
    if status.forced_open:
        return "Market open · opened manually"
    return market_text(status)


@bp.get("/admin")
@admin_required
def index():
    return _page(**_pages(request.args))


# --- Users (R3, R18.9) -------------------------------------------------------------------------------

@bp.post("/admin/users")
@admin_required
def add_user():
    values = {k: request.form.get(k, "") for k in USER_FIELDS}
    try:
        user_id, temporary_password = users.create_user(
            current_user.user_id, values["name"], values["ssn"], values["email"], _dob(values["date_of_birth"]),
            values["role"], values["access_level"] or None, values["department"] or None,
        )  # R3.1-R3.6, R3.9
    except ValidationFailed as exc:  # the SSN box is never refilled (template), so an SSN isn't shown again (R3.6)
        return _page(forms={"add_user": values}, errors={"add_user": {exc.field: exc.message}})
    created = users.get_user(user_id)
    return _page(notice={"title": f"{created.role} {created.name} ({created.email}) created.",
                         "password": temporary_password})  # R3.4: shown once


@bp.post("/admin/users/<int:user_id>/edit")
@admin_required
def edit_user(user_id: int):
    values = {k: request.form.get(k, "") for k in ("name", "email", "date_of_birth")}
    key = f"user-{user_id}"
    try:
        updated = users.update_user(current_user.user_id, user_id, values["name"], values["email"],
                                    _dob(values["date_of_birth"]))  # R3.12, D20
    except ValidationFailed as exc:
        return _page(forms={key: values}, errors={key: {exc.field: exc.message}}, editing=key, **_pages(request.form))
    flash(f"Saved changes to {updated.name}.", "success")
    return _back("users")


@bp.post("/admin/users/<int:user_id>/deactivate")
@admin_required
def deactivate_user(user_id: int):
    try:
        users.deactivate_user(current_user.user_id, user_id)  # R3.10, D7
    except ValidationFailed as exc:  # an administrator can't deactivate themselves
        flash(exc.message, "error")
        return _back("users")
    flash(f"{users.get_user(user_id).name} was deactivated and can no longer log in. Their records are kept.", "success")
    return _back("users")


@bp.post("/admin/users/<int:user_id>/temporary-password")
@admin_required
def issue_temporary_password(user_id: int):
    try:
        temporary_password = users.issue_temporary_password(current_user.user_id, user_id)  # R18.9
    except ValidationFailed as exc:  # not for yourself (R18.10)
        flash(exc.message, "error")
        return _back("users")
    target = users.get_user(user_id)
    return _page(notice={"title": f"New temporary password for {target.name} ({target.email}). "
                                  "Their existing logins have been ended.",
                         "password": temporary_password}, **_pages(request.form))


# --- Stocks (R14, D16, D20) ----------------------------------------------------------------------------

@bp.post("/admin/stocks")
@admin_required
def add_stock():
    values = {k: request.form.get(k, "") for k in STOCK_FIELDS}
    randomize = request.form.get("randomize_history") == "on"
    try:
        stocks.add_stock(
            current_user.user_id, values["company_name"], values["symbol"], _money(values["price"], "price"),
            values["description"] or None, values["exchange"] or None, randomize,
            _shares(values["shares"], "shares"),
        )  # R14.2-R14.6
    except ValidationFailed as exc:
        values["randomize_history"] = "on" if randomize else ""
        return _page(forms={"add_stock": values}, errors={"add_stock": {exc.field: exc.message}})
    except DomainError as exc:  # Symbol already exists (R14.4)
        values["randomize_history"] = "on" if randomize else ""
        return _page(forms={"add_stock": values}, errors={"add_stock": {"symbol": exc.message}})
    flash(f"Stock {stocks.normalize_symbol(values['symbol'])} added"
          f"{' with a year of price history' if randomize else ''}. It can be traded now.", "success")  # R14.5
    return redirect(url_for("admin.index"))


@bp.post("/admin/stocks/<int:issuer_id>/edit")
@admin_required
def edit_stock(issuer_id: int):
    values = {k: request.form.get(k, "") for k in ("company_name", "description", "exchange")}
    key = f"stock-{issuer_id}"
    try:
        updated = stocks.update_stock(current_user.user_id, issuer_id, values["company_name"],
                                      values["description"] or None, values["exchange"] or None)  # R14.8
    except ValidationFailed as exc:
        return _page(forms={key: values}, errors={key: {exc.field: exc.message}}, editing=key, **_pages(request.form))
    flash(f"Saved changes to {updated.symbol}.", "success")
    return _back("stocks")


@bp.post("/admin/stocks/<int:issuer_id>/delist")
@admin_required
def delist_stock(issuer_id: int):
    stocks.delist_stock(current_user.user_id, issuer_id)  # R14.9, D20 (NotFound -> 404)
    flash("Stock delisted. It can't be found or bought any more; customers who hold it can still sell.", "success")
    return _back("stocks")


# --- Market Time (R15, D21, D29) -------------------------------------------------------------------------

@bp.post("/admin/market-schedule")
@admin_required
def save_schedule():
    values = {k: request.form.get(k, "") for k in ("open_time", "close_time")}
    values["trading_days"] = request.form.getlist("trading_days")
    values["force_open"] = "on" if request.form.get("force_open") == "on" else ""
    try:
        market.save_schedule(current_user.user_id, _time(values["open_time"], "open_time", "open time"),
                             _time(values["close_time"], "close_time", "close time"),
                             _days(values["trading_days"]), values["force_open"] == "on")  # R15.3-R15.5, R15.7, R15.8
    except ValidationFailed as exc:
        return _page(forms={"schedule": values}, errors={"schedule": {exc.field: exc.message}})
    schedule = market.current_schedule()
    message = (f"Market hours saved: {schedule.open_time.strftime('%I:%M %p').lstrip('0')} to "
               f"{schedule.close_time.strftime('%I:%M %p').lstrip('0')} Arizona time, "
               f"{market.days_text(schedule.trading_days)}.")
    if schedule.force_open:
        message += " Force market open is on: the market stays open until it is switched off."
    flash(message, "success")
    return redirect(url_for("admin.index"))


def init_admin(app: Flask) -> None:
    app.register_blueprint(bp)
