"""The Administration page: add and manage users, add and manage stocks, and set the market hours.
Administrators only.

A new user gets a temporary password. It is shown once, on the page that answers the form, and
the user must choose their own password when they first log in. If someone forgets their password,
an administrator gives them a new temporary one the same way.
"""
import random
import re
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app import market
from app.auth import admin_required, hash_password, password_problem
from app.common import (ONE_CENT, UserError, check_digits, check_text, encrypt, make_page, parse_money,
                        parse_shares, percent, to_cents, today)
from app.models import Admin, Issuer, MarketSchedule, StockPrice, User, db

bp = Blueprint("admin", __name__)

ROLES = ["Customer", "Administrator"]
DESCRIPTION_MAX_LENGTH = 50
ROWS_PER_PAGE = 10

# The Market Control buttons: (value saved, button label, Bootstrap colour).
MARKET_CONTROL_CHOICES = [
    (market.FOLLOW_SCHEDULE, "Schedule", "secondary"),
    (market.FORCE_OPEN, "Force open", "success"),
    (market.FORCE_CLOSED, "Force closed", "danger"),
]


# --- Checking the Add User and Add Stocks forms -------------------------------------------------

SYMBOL_PATTERN = re.compile(r"[A-Z0-9]{1,10}")
SSN_PATTERN = r"[0-9]{3}-[0-9]{2}-[0-9]{4}"


def check_email(typed, field="email"):
    """An email address, in lower case. (The box is type="email", so the browser checks its format.)"""
    cleaned = (typed or "").strip().lower()
    if cleaned == "":
        raise UserError("Email is required.", field)
    return cleaned


def check_date_of_birth(typed, field="date_of_birth"):
    """A date of birth typed as MM/DD/YYYY."""
    try:
        return datetime.strptime((typed or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        raise UserError("Enter the date of birth as MM/DD/YYYY.", field)


def check_symbol(typed, field="symbol"):
    """A stock symbol: 1 to 10 letters or digits, in capitals."""
    cleaned = (typed or "").strip().upper()
    if not SYMBOL_PATTERN.fullmatch(cleaned):
        raise UserError("Symbol must be 1 to 10 letters (A-Z) or digits.", field)
    return cleaned


def make_temporary_password():
    """A random password for a new user, shown once to the administrator."""
    return secrets.token_urlsafe(12)


# --- Creating users and stocks -------------------------------------------------------------------

def create_user(name, ssn, email, date_of_birth, password, temporary=True, admin_details=None):
    """Save a new user and return it.

    admin_details is (access_level, department) to make them an administrator, or None for a
    customer. Raises UserError if the email is already used.
    """
    user = User(name=name, date_of_birth=date_of_birth, ssn=encrypt(ssn), email=email,
                password_hash=hash_password(password), must_change_password=temporary)
    if admin_details is not None:
        access_level, department = admin_details
        user.admin = Admin(access_level=access_level, department=department)

    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        # The database refused it because the email column must be unique.
        db.session.rollback()
        raise UserError("A user with this email already exists.", "email")
    return user


def create_first_admin(email, password):
    """Create the first administrator from ADMIN_EMAIL and ADMIN_PASSWORD in .env.

    Called every time the site starts, but it only does something when there is no administrator
    yet and both settings are filled in. There is no sign-up page, so this is how the very first
    administrator gets in; after that, administrators create everyone else on the Administration page.

    The name, SSN and date of birth are placeholders. Change the name and date of birth on the
    Administration page after logging in. (The SSN is never shown anywhere.)
    """
    if not email or not password:
        return
    if Admin.query.first() is not None:
        return  # there is already an administrator

    problem = password_problem(password)
    if problem is not None:
        raise RuntimeError("ADMIN_PASSWORD in .env isn't allowed: " + problem)
    try:
        create_user("Administrator", "000-00-0000", email.strip().lower(), date(1900, 1, 1), password,
                    temporary=False, admin_details=("admin", None))
    except UserError as error:
        raise RuntimeError("Couldn't create the administrator from .env: " + error.message)


def made_up_closing_prices(price, number_of_days):
    """Made-up closing prices for the days before today, oldest first, ending with today's `price`.

    It walks backwards from today: each day before is up to 2.5% higher or lower than the day after.
    """
    closes = [price]
    for _ in range(number_of_days):
        change = random.uniform(-0.025, 0.025)
        day_before = to_cents(closes[-1] / Decimal(1 + change))
        closes.append(max(ONE_CENT, day_before))
    closes.reverse()  # oldest first
    return closes


def past_weekdays(number_of_days):
    """The weekdays (Monday to Friday) in the last `number_of_days` days, oldest first."""
    days = []
    for days_ago in range(number_of_days, 0, -1):
        day = today() - timedelta(days=days_ago)
        if day.weekday() < 5:  # 0-4 are Monday to Friday
            days.append(day)
    return days


def create_stock(company_name, symbol, price, shares, description=None, exchange=None,
                 random_history=True):
    """Save a new stock and return it.

    With random_history=True it also gets a year of made-up daily prices, so its chart isn't empty.
    """
    if Issuer.query.filter_by(symbol=symbol).first() is not None:
        raise UserError("Symbol already exists", "symbol")

    stock = Issuer(company_name=company_name, symbol=symbol, exchange=exchange, current_price=price,
                   description=description, shares_available=shares)
    db.session.add(stock)
    db.session.flush()  # sends the new row to the database now, which gives the stock its issuer_id

    days = []
    if random_history:
        days = past_weekdays(365)
    days.append(today())

    # closes[0] is the oldest day's closing price, closes[-1] is today's (the price given).
    closes = made_up_closing_prices(price, len(days) - 1)
    for i in range(len(days)):
        if i == 0:
            open_price = closes[0]
        else:
            open_price = closes[i - 1]  # each day opens at the day before's close
        db.session.add(StockPrice(issuer_id=stock.issuer_id, price_date=days[i],
                                  open_price=open_price, close_price=closes[i]))
    db.session.commit()
    return stock


# --- Market hours in words, for the Market Time card --------------------------------------------

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def group_days(day_numbers):
    """Group days that follow each other: {0, 1, 2, 4} -> [[0, 1, 2], [4]]."""
    groups = []
    for day in sorted(day_numbers):
        if len(groups) > 0 and groups[-1][-1] == day - 1:
            groups[-1].append(day)  # the day after the last one: same group
        else:
            groups.append([day])  # start a new group
    return groups


def days_text(day_numbers):
    """The trading days in words: "Monday to Friday", "Monday, Wednesday and Friday", "every day"."""
    if len(day_numbers) == 7:
        return "every day"
    parts = []
    for group in group_days(day_numbers):
        if len(group) >= 3:
            parts.append(DAY_NAMES[group[0]] + " to " + DAY_NAMES[group[-1]])
        else:
            for day in group:
                parts.append(DAY_NAMES[day])
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# --- The page ----------------------------------------------------------------------------------------

def stock_list_page(page_number):
    """One page of the Stocks list: listed stocks first, then delisted ones."""
    query = Issuer.query.order_by(Issuer.is_listed.desc(), Issuer.symbol)
    page = make_page(query, page_number, ROWS_PER_PAGE)

    rows = []
    for stock in page["rows"]:
        previous_close = stock.previous_close(today())
        rows.append({
            "issuer_id": stock.issuer_id,
            "symbol": stock.symbol,
            "company_name": stock.company_name,
            "exchange": stock.exchange,
            "description": stock.description,
            "current_price": stock.current_price,
            "shares_available": stock.shares_available,
            "is_listed": stock.is_listed,
            "change_pct": percent(stock.current_price - previous_close, previous_close),
        })
    page["rows"] = rows
    return page


def market_state_text(status):
    """The status line on the Market Time card."""
    if status["forced_open"]:
        return "Market open · opened manually"
    if status["forced_closed"]:
        return "Market closed · closed manually"
    return market.status_text(status)


def show_admin_page(forms=None, errors=None, notice=None, editing=None, users_page=1, stocks_page=1):
    """The Administration page.

    forms     what was typed into a form that had an error, so it can be shown again.
    errors    the error messages. Both are keyed by the form's name: "add_user", "add_stock",
              "schedule", "user-<id>" or "stock-<id>".
    notice    a temporary password to show once: {"title": ..., "password": ...}.
    editing   which Edit row to keep open (after an error in it).
    """
    if forms is None:
        forms = {}
    if errors is None:
        errors = {}

    schedule = market.get_schedule()
    status = market.get_status()

    # The Trading Days and Market Control to show as chosen: what was just submitted if the
    # Market Time form had an error, otherwise what is saved.
    if "schedule" in forms:
        chosen_days = set()
        for day in forms["schedule"]["trading_days"]:
            if day.isdigit():
                chosen_days.add(int(day))
        chosen_override = forms["schedule"]["market_override"]
    else:
        chosen_days = schedule["trading_days"]
        chosen_override = schedule["override"]

    user_page = make_page(User.query.order_by(User.name, User.user_id), users_page, ROWS_PER_PAGE)
    stock_page = stock_list_page(stocks_page)

    html = render_template(
        "admin/index.html",
        user_page=user_page,
        user_page_numbers=user_page["numbers"],
        stock_page=stock_page,
        stock_page_numbers=stock_page["numbers"],
        schedule=schedule,
        weekday_names=DAY_NAMES,
        chosen_days=chosen_days,
        chosen_override=chosen_override,
        override_choices=MARKET_CONTROL_CHOICES,
        days_text=days_text,
        market_open=status["is_open"],
        market_state=market_state_text(status),
        forms=forms,
        errors=errors,
        notice=notice,
        editing=editing,
        roles=ROLES,
        description_max=DESCRIPTION_MAX_LENGTH,
    )
    response = make_response(html)
    if notice is not None:
        # Tell the browser not to keep a copy of a page with a password on it.
        response.headers["Cache-Control"] = "no-store"
    return response


def show_form_error(form_name, typed, error, editing=False):
    """Show the Administration page again, with the form's error next to the right box.

    form_name  which form had the error, like "add_user" or "user-7"
    typed      what was typed into it, so it can be filled back in
    editing    True for the Edit forms in the lists, which are folded away unless we open them
    """
    forms = {form_name: typed}
    errors = {form_name: {error.field: error.message}}
    pages = page_numbers_from(request.form)
    editing_form = None
    if editing:
        editing_form = form_name
    return show_admin_page(forms=forms, errors=errors, editing=editing_form,
                           users_page=pages["users_page"], stocks_page=pages["stocks_page"])


def page_numbers_from(form_or_url):
    """Which page of the Users and Stocks lists was showing, so we can go back to it."""
    return {
        "users_page": form_or_url.get("users_page", 1, type=int),
        "stocks_page": form_or_url.get("stocks_page", 1, type=int),
    }


def back_to_admin_page(section):
    """Reload the Administration page at the same list pages, scrolled to `section`."""
    pages = page_numbers_from(request.form)
    return redirect(url_for("admin.index", users_page=pages["users_page"],
                            stocks_page=pages["stocks_page"], _anchor=section))


@bp.get("/admin")
@admin_required
def index():
    pages = page_numbers_from(request.args)
    return show_admin_page(users_page=pages["users_page"], stocks_page=pages["stocks_page"])


# --- Users -------------------------------------------------------------------------------------------

@bp.post("/admin/users")
@admin_required
def add_user():
    typed = {}
    for field in ["name", "ssn", "email", "date_of_birth", "role", "access_level", "department"]:
        typed[field] = request.form.get(field, "")

    try:
        name = check_text(typed["name"], "name", "Full name")
        ssn = check_digits(typed["ssn"], "ssn", SSN_PATTERN, "Enter the SSN as XXX-XX-XXXX.")
        email = check_email(typed["email"])
        date_of_birth = check_date_of_birth(typed["date_of_birth"])
        admin_details = None
        if typed["role"] == "Administrator":
            access_level = check_text(typed["access_level"], "access_level", "Access level")
            department = check_text(typed["department"], "department", "Department", required=False)
            admin_details = (access_level, department)

        password = make_temporary_password()
        create_user(name, ssn, email, date_of_birth, password, admin_details=admin_details)
    except UserError as error:
        # The SSN box is never filled back in, so an SSN is never shown again.
        return show_form_error("add_user", typed, error)

    role = "Customer"
    if admin_details is not None:
        role = "Administrator"
    notice = {"title": f"{role} {name} ({email}) created.", "password": password}
    return show_admin_page(notice=notice)


@bp.post("/admin/users/<int:user_id>/edit")
@admin_required
def edit_user(user_id):
    """Change a user's name, email and date of birth. The SSN and role can't be changed."""
    user = db.get_or_404(User, user_id)
    typed = {
        "name": request.form.get("name", ""),
        "email": request.form.get("email", ""),
        "date_of_birth": request.form.get("date_of_birth", ""),
    }
    form_name = f"user-{user_id}"

    try:
        user.name = check_text(typed["name"], "name", "Full name")
        user.email = check_email(typed["email"])
        user.date_of_birth = check_date_of_birth(typed["date_of_birth"])
        try:
            db.session.commit()
        except IntegrityError:
            raise UserError("A user with this email already exists.", "email")
    except UserError as error:
        db.session.rollback()  # forget the changes
        return show_form_error(form_name, typed, error, editing=True)

    flash(f"Saved changes to {user.name}.", "success")
    return back_to_admin_page("users")


@bp.post("/admin/users/<int:user_id>/deactivate")
@admin_required
def deactivate_user(user_id):
    """Stop a user logging in. Their records are kept (nothing is deleted)."""
    user = db.get_or_404(User, user_id)
    user.is_active = False
    db.session.commit()
    flash(f"{user.name} was deactivated and can no longer log in. Their records are kept.", "success")
    return back_to_admin_page("users")


@bp.post("/admin/users/<int:user_id>/temporary-password")
@admin_required
def issue_temporary_password(user_id):
    """For a forgotten password: replace it with a new temporary one, shown once."""
    user = db.get_or_404(User, user_id)
    password = make_temporary_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    db.session.commit()

    notice = {"title": f"New temporary password for {user.name} ({user.email}).", "password": password}
    pages = page_numbers_from(request.form)
    return show_admin_page(notice=notice, users_page=pages["users_page"],
                           stocks_page=pages["stocks_page"])


# --- Stocks ------------------------------------------------------------------------------------------

@bp.post("/admin/stocks")
@admin_required
def add_stock():
    typed = {}
    for field in ["company_name", "symbol", "price", "shares", "exchange", "description"]:
        typed[field] = request.form.get(field, "")
    random_history = request.form.get("randomize_history") == "on"

    try:
        company_name = check_text(typed["company_name"], "company_name", "Stock name")
        description = check_text(typed["description"], "description", "Description", required=False)
        exchange = check_text(typed["exchange"], "exchange", "Exchange", required=False)
        symbol = check_symbol(typed["symbol"])
        price = parse_money(typed["price"], "price")
        shares = parse_shares(typed["shares"], "shares")
        create_stock(company_name, symbol, price, shares, description, exchange, random_history)
    except UserError as error:
        if random_history:
            typed["randomize_history"] = "on"
        else:
            typed["randomize_history"] = ""
        return show_form_error("add_stock", typed, error)

    if random_history:
        flash(f"Stock {symbol} added with a year of price history. It can be traded now.", "success")
    else:
        flash(f"Stock {symbol} added. It can be traded now.", "success")
    return redirect(url_for("admin.index"))


@bp.post("/admin/stocks/<int:issuer_id>/edit")
@admin_required
def edit_stock(issuer_id):
    """Change a stock's name, description and exchange. The symbol and price can't be changed."""
    stock = db.get_or_404(Issuer, issuer_id)
    typed = {
        "company_name": request.form.get("company_name", ""),
        "description": request.form.get("description", ""),
        "exchange": request.form.get("exchange", ""),
    }
    form_name = f"stock-{issuer_id}"

    try:
        company_name = check_text(typed["company_name"], "company_name", "Stock name")
        description = check_text(typed["description"], "description", "Description", required=False)
        exchange = check_text(typed["exchange"], "exchange", "Exchange", required=False)
    except UserError as error:
        return show_form_error(form_name, typed, error, editing=True)

    stock.company_name = company_name
    stock.description = description
    stock.exchange = exchange
    db.session.commit()
    flash(f"Saved changes to {stock.symbol}.", "success")
    return back_to_admin_page("stocks")


@bp.post("/admin/stocks/<int:issuer_id>/delist")
@admin_required
def delist_stock(issuer_id):
    """Take a stock off the market: it can't be found or bought, but people who own it can still sell."""
    stock = db.get_or_404(Issuer, issuer_id)
    stock.is_listed = False
    db.session.commit()
    flash("Stock delisted. It can't be found or bought any more; customers who hold it can still sell.",
          "success")
    return back_to_admin_page("stocks")


# --- Market hours ------------------------------------------------------------------------------------

def check_time(typed, field, label):
    """A time from an <input type="time"> box, which sends text like "09:30"."""
    try:
        return datetime.strptime((typed or "").strip(), "%H:%M").time()
    except ValueError:
        raise UserError(f"Enter the {label} as HH:MM.", field)


@bp.post("/admin/market-schedule")
@admin_required
def save_schedule():
    typed = {
        "open_time": request.form.get("open_time", ""),
        "close_time": request.form.get("close_time", ""),
        "trading_days": request.form.getlist("trading_days"),  # one value per ticked box
        "market_override": request.form.get("market_override", market.FOLLOW_SCHEDULE),
    }
    try:
        open_time = check_time(typed["open_time"], "open_time", "open time")
        close_time = check_time(typed["close_time"], "close_time", "close time")
        if close_time <= open_time:
            raise UserError("Close time must be after open time.", "close_time")

        days = set()
        for day in range(7):
            if str(day) in typed["trading_days"]:
                days.add(day)
        if len(days) == 0:
            raise UserError("Select at least one trading day.", "trading_days")
    except UserError as error:
        return show_form_error("schedule", typed, error)

    # Save the days as one number (see MarketSchedule in models.py): day n adds 2 to the power n.
    days_number = 0
    for day in days:
        days_number += 2 ** day

    # Anything other than Force open or Force closed means "follow the schedule".
    override = typed["market_override"]
    if override != market.FORCE_OPEN and override != market.FORCE_CLOSED:
        override = market.FOLLOW_SCHEDULE

    schedule = MarketSchedule(open_time=open_time, close_time=close_time, trading_days=days_number,
                              market_override=override,
                              updated_by=current_user.user_id)
    db.session.add(schedule)
    db.session.commit()

    message = (f"Market hours saved: {market.clock_text(open_time)} to {market.clock_text(close_time)} "
               f"Arizona time, {days_text(days)}.")
    if override == market.FORCE_OPEN:
        message += " Force open is on until Schedule is chosen again."
    elif override == market.FORCE_CLOSED:
        message += " Force closed is on until Schedule is chosen again."
    flash(message, "success")
    return redirect(url_for("admin.index"))