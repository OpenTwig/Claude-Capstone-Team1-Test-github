"""Transaction History page (wf-8, R13). Customers see their own portfolios; administrators can
choose any customer's (R13.6, D19). Filters are GET parameters, so a result can be bookmarked
and printed (design: History)."""
from __future__ import annotations

from flask import Blueprint, Flask, abort, render_template, request
from flask_login import current_user

from app.errors import ValidationFailed
from app.services import history
from app.services.paging import page_number, page_numbers

bp = Blueprint("history", __name__)

ALL_CHOICE = "all"  # ?portfolio=all: every customer's history, administrators only (user feedback)


@bp.get("/history")
def history_page():
    options = history.choices(current_user.user_id)
    range_key = request.args.get("range", history.DEFAULT_RANGE)
    if range_key not in history.RANGES:
        range_key = history.DEFAULT_RANGE
    symbol = request.args.get("symbol", "").strip()
    page = dict(options=options, ranges=history.RANGES, range_key=range_key, symbol=symbol,
                selected=None, rows=None, start=None, end=None, errors={}, is_admin=current_user.is_admin,
                all_choice=ALL_CHOICE, print_view=False, listing=None, numbers=[])
    if not options:
        return render_template("history/history.html", **page)

    is_admin = current_user.is_admin
    raw = request.args.get("portfolio", "")
    if is_admin and raw in ("", ALL_CHOICE):
        portfolio_id, selected = history.ALL, ALL_CHOICE  # administrators start with every customer (R13.6)
    elif raw.isdigit():
        portfolio_id = selected = int(raw)
    elif raw:
        abort(404)  # "all" or junk from a customer: never anyone else's history (R13.7)
    else:
        portfolio_id = selected = options[0].portfolio_id
    start, end = history.date_range(range_key, history.today_in_market())
    page.update(selected=selected, start=start, end=end)
    # ?print=1: every row in the range on one page, for the printout the user asked for (R13.5);
    # otherwise one page of rows (user feedback: the list mustn't grow without end).
    print_view = request.args.get("print") == "1"
    page["print_view"] = print_view
    try:
        if print_view:
            page["rows"] = history.search_history(current_user.user_id, portfolio_id, start, end, symbol)  # R13.2
        else:
            listing = history.history_page(current_user.user_id, portfolio_id, start, end, symbol,
                                           page=page_number(request.args.get("page")))
            page.update(rows=list(listing.rows), listing=listing,
                        numbers=page_numbers(listing.page, listing.pages))
    except ValidationFailed as exc:
        page["errors"] = {exc.field: exc.message}
    # NotFound (someone else's portfolio, R13.7) propagates to the 404 page.
    return render_template("history/history.html", **page)


def init_history(app: Flask) -> None:
    app.register_blueprint(bp)
