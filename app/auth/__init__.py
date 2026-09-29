"""Login, logout, the login requirement for every page, and the first-login password change
(R1, R2, R18; design: Authentication and sessions)."""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, abort, current_app, redirect, request, url_for
from flask_login import LoginManager, UserMixin, current_user

from app.services import users
from app.services.types import UserRow

bp = Blueprint("auth", __name__)
login_manager = LoginManager()
login_manager.login_view = "auth.signin"
login_manager.login_message = None  # no "please log in" notice: the login page speaks for itself

# Every page requires login (R1.1) unless its view is marked @public on purpose.
PUBLIC_ENDPOINTS = {"static"}  # Flask's built-in static file view can't carry the marker


def public(view):
    """Mark a view as reachable without logging in. Use sparingly: login page, health check."""
    view.is_public = True
    return view


class LoginUser(UserMixin):
    """Flask-Login's view of a user; other attributes come from the UserRow."""

    def __init__(self, row: UserRow):
        self.row = row

    def get_id(self) -> str:
        # "<userID>:<session_version>": bumping the version (password change, new temporary
        # password) makes every existing session and remember cookie stop matching (D10, R18.7).
        return f"{self.row.user_id}:{self.row.session_version}"

    @property
    def is_active(self) -> bool:
        return self.row.is_active

    def __getattr__(self, name):
        return getattr(self.row, name)


@login_manager.user_loader
def load_user(login_id: str) -> LoginUser | None:
    """Deactivated users, and logins from before a password change, get None: signed out (R3.10, R18.7)."""
    try:
        user_id, version = (int(part) for part in login_id.split(":"))
    except (AttributeError, TypeError, ValueError):
        return None
    row = users.get_user(user_id)
    if row is None or not row.is_active or row.session_version != version:
        return None
    return LoginUser(row)


@bp.before_app_request
def require_login():
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if getattr(current_app.view_functions.get(request.endpoint), "is_public", False):
        return None
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    return None


# Reachable while a password change is required (R18.3). Registered after require_login, so it runs second.
PASSWORD_CHANGE_ENDPOINTS = {"auth.change_password", "auth.logout"}


@bp.before_app_request
def require_password_change():
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS | PASSWORD_CHANGE_ENDPOINTS:
        return None
    if getattr(current_app.view_functions.get(request.endpoint), "is_public", False):
        return None
    if current_user.is_authenticated and current_user.must_change_password:
        return redirect(url_for("auth.change_password"))
    return None


def admin_required(view):
    """Administrator-only pages: others get 403, enforced on the server (R2.4)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def customer_required(view):
    """Customer-only pages (Account, Positions, Trade, Transfer Funds): administrators get 403 (D19, R2.10)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def landing_url(user) -> str:
    """Administrators land on Administration, customers on Account (R1.6, R1.7)."""
    return url_for("admin.index") if user.is_admin else url_for("account.summary")


def init_auth(app) -> None:
    login_manager.init_app(app)
    from app.auth import routes  # noqa: F401  (registers the views on bp)

    app.register_blueprint(bp)
