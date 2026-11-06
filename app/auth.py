"""Logging in and out, choosing a new password, and who may open which page.

There is no sign-up page. An administrator creates each user with a temporary password,
and the user must choose their own password the first time they log in.
"""
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, current_user, login_user, logout_user

from app.models import User, db

bp = Blueprint("auth", __name__)
bcrypt = Bcrypt()

login_manager = LoginManager()
login_manager.login_view = "auth.signin"  # where to send people who aren't logged in
login_manager.login_message = None  # don't flash "Please log in" on the login page

# Pages anyone can open without logging in. Every other page needs a login.
PUBLIC_PAGES = ["auth.signin", "static", "health"]

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_BYTES = 72  # bcrypt can't hash passwords longer than this


# --- Passwords ---------------------------------------------------------------------------------------

def hash_password(password):
    """The bcrypt hash of a password. We store this, never the password itself."""
    return bcrypt.generate_password_hash(password).decode("utf-8")


def password_matches(stored_hash, typed_password):
    """True if the typed password is the one that `stored_hash` was made from."""
    if len(typed_password.encode()) > PASSWORD_MAX_BYTES:
        return False  # too long for bcrypt, so it can't be anyone's password
    return bcrypt.check_password_hash(stored_hash, typed_password)


def password_problem(password):
    """What is wrong with a new password, or None if it is fine."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password.encode()) > PASSWORD_MAX_BYTES:
        return f"Password must be at most {PASSWORD_MAX_BYTES} characters."
    return None


# --- Who may open which page -------------------------------------------------------------------------

@login_manager.user_loader
def load_user(user_id):
    """Flask-Login calls this on every request to find the logged-in user."""
    user = db.session.get(User, int(user_id))
    if user is None or not user.is_active:
        return None  # a deactivated user is logged out
    return user


@bp.before_app_request
def require_login():
    """Runs before every page.

    Sends anyone who isn't logged in to the login page, and anyone still on a temporary
    password to the "choose a new password" page.
    """
    page = request.endpoint
    if page is None or page in PUBLIC_PAGES:
        return None  # no login needed (None means "carry on to the page")

    if not current_user.is_authenticated:
        return login_manager.unauthorized()  # go to the login page

    allowed_pages = ["auth.change_password", "auth.logout"]
    if current_user.must_change_password and page not in allowed_pages:
        return redirect(url_for("auth.change_password"))
    return None


def admin_required(page_function):
    """Put @admin_required above a page that only administrators may open. Others see 403."""
    @wraps(page_function)
    def check_then_show_page(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return page_function(*args, **kwargs)
    return check_then_show_page


def customer_required(page_function):
    """Put @customer_required above a page that only customers may open.

    Administrators run the system but don't trade, so they see 403 on these pages.
    """
    @wraps(page_function)
    def check_then_show_page(*args, **kwargs):
        if current_user.is_admin:
            abort(403)
        return page_function(*args, **kwargs)
    return check_then_show_page


def landing_page(user):
    """Where a user goes after logging in."""
    if user.is_admin:
        return url_for("admin.index")
    return url_for("account.summary")


# --- Pages -------------------------------------------------------------------------------------------

@bp.get("/")
def home():
    return redirect(landing_page(current_user))


@bp.route("/signin", methods=["GET", "POST"])
def signin():
    if current_user.is_authenticated:
        return redirect(landing_page(current_user))

    email = request.form.get("email", "").strip().lower()
    error = None

    if request.method == "POST":
        typed_password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user is not None and user.is_active and password_matches(user.password_hash, typed_password):
            session.clear()  # start a fresh session

            # "Stay signed in" keeps the login after the browser closes. Not for temporary passwords.
            remember = request.form.get("stay_signed_in") is not None
            if user.must_change_password:
                remember = False

            login_user(user, remember=remember)
            return redirect(landing_page(user))

        # The same message whether the email or the password was wrong,
        # so nobody can use this page to find out which emails have accounts.
        error = "Invalid email or password"

    return render_template("auth/signin.html", error=error, email=email)


@bp.route("/change-password", methods=["GET", "POST"])
def change_password():
    """Where users on a temporary password choose their own."""
    if not current_user.must_change_password:
        return redirect(landing_page(current_user))

    errors = {}
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if password_problem(new_password):
            errors["new_password"] = password_problem(new_password)
        elif new_password != confirm_password:
            errors["confirm_password"] = "The two passwords don't match."
        elif password_matches(current_user.password_hash, new_password):
            errors["new_password"] = "Choose a password different from your temporary one."
        else:
            current_user.password_hash = hash_password(new_password)
            current_user.must_change_password = False
            db.session.commit()
            flash("Your password has been changed.", "success")
            return redirect(landing_page(current_user))

    return render_template("auth/change_password.html", errors=errors, min_length=PASSWORD_MIN_LENGTH)


@bp.post("/logout")
def logout():
    session.clear()
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.signin"))
