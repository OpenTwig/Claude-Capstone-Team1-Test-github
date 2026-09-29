from flask import flash, redirect, render_template, session, url_for
from flask_login import current_user, login_user, logout_user

from app.auth import LoginUser, bp, landing_url, public
from app.auth.forms import ChangePasswordForm, LoginForm
from app.errors import DomainError, ValidationFailed
from app.security import limiter
from app.services import users
from app.services.validation import PASSWORD_MIN


@bp.get("/")
def home():
    return redirect(landing_url(current_user))


@bp.route("/signin", methods=["GET", "POST"])
@public
@limiter.limit("10 per minute", methods=["POST"])  # R1.9
def signin():
    if current_user.is_authenticated:
        return redirect(landing_url(current_user))
    form = LoginForm()
    error = None
    if form.validate_on_submit():
        try:
            user = users.authenticate(form.email.data, form.password.data)
        except DomainError as exc:
            error = exc.message  # R1.4, R1.8, R18.8
        else:
            session.clear()  # drop anything from before login
            # A temporary password never gets a long-lived login (R18.4).
            remember = form.stay_signed_in.data and not user.must_change_password
            login_user(LoginUser(user), remember=remember)  # R1.5
            if user.must_change_password:
                return redirect(url_for("auth.change_password"))  # R18.3
            # Always land by role; a ?next= address is ignored so login links can't redirect elsewhere.
            return redirect(landing_url(user))
    return render_template("auth/signin.html", form=form, error=error)


@bp.route("/change-password", methods=["GET", "POST"])
def change_password():
    """First-login password change (R18.3-R18.7). There is no other way to change a password (D10)."""
    if not current_user.must_change_password:
        return redirect(landing_url(current_user))
    form = ChangePasswordForm()
    errors: dict[str, str] = {}
    if form.validate_on_submit():
        try:
            user = users.change_password(current_user.user_id, form.new_password.data, form.confirm_password.data)
        except ValidationFailed as exc:
            errors[exc.field] = exc.message
        else:
            login_user(LoginUser(user))  # this browser stays signed in under the new session version
            flash("Your password has been changed.", "success")
            return redirect(landing_url(user))
    return render_template("auth/change_password.html", form=form, errors=errors, min_length=PASSWORD_MIN)


@bp.post("/logout")
def logout():
    session.clear()
    logout_user()  # after clear(), so its "clear the remember cookie" flag survives
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.signin"))  # R1.10
