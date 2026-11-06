"""Builds the Flask app. Running `flask --app app run` calls create_app() below.

The settings (passwords and keys) come from a .env file, so they are never written in the code.
Copy .env.example to .env and fill it in.
"""
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import URL, text

# The settings that must be in .env.
REQUIRED_SETTINGS = [
    "SECRET_KEY",
    "DB_HOST",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "FIELD_ENCRYPTION_KEY",
]

# The friendly error pages: status code -> (title, message). We never show Python errors to users.
ERROR_PAGES = {
    400: ("Request not accepted", "Your form expired or was submitted incorrectly. "
                                  "Go back, refresh the page, and try again."),
    403: ("Access denied", "You don't have permission to view this page."),
    404: ("Page not found", "The page you asked for doesn't exist."),
    405: ("Not allowed", "That action isn't allowed here."),
    500: ("Something went wrong", "Something went wrong. Please try again."),
}

def database_address():
    """The address SQLAlchemy uses to reach MySQL: mysql+pymysql://user:password@host:port/database.

    URL.create() builds it safely even if the password contains characters like @ or /.
    """
    return URL.create(
        "mysql+pymysql",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "3306")),
        database=os.environ["DB_NAME"],
        query={"charset": "utf8mb4"},
    )


def create_app():
    load_dotenv()  # read .env into the environment
    missing = []
    for setting in REQUIRED_SETTINGS:
        if not os.environ.get(setting):
            missing.append(setting)
    if missing:
        raise RuntimeError("Set these in .env (copy .env.example to start): " + ", ".join(missing))

    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
    app.config["FIELD_ENCRYPTION_KEY"] = os.environ["FIELD_ENCRYPTION_KEY"]
    app.config["SQLALCHEMY_DATABASE_URI"] = database_address()
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        # Store every time in UTC.
        "connect_args": {"init_command": "SET time_zone = '+00:00'"},
        # Lets two customers buy stocks at the same moment without blocking each other.
        "isolation_level": "READ COMMITTED",
        # Reconnect if MySQL closed a connection that sat unused for a long time.
        "pool_pre_ping": True,
    }
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

    # Every form that uses POST must include {{ csrf_token() }}. This stops other websites
    # from submitting forms to our site on a logged-in user's behalf.
    CSRFProtect(app)

    # These are imported here, inside the function, because they need the settings above.
    from app import account, admin, auth, common, history, stocks, trade, transfer
    from app.models import db

    db.init_app(app)
    auth.login_manager.init_app(app)
    auth.bcrypt.init_app(app)

    with app.app_context():
        db.create_all()  # create any tables that don't exist yet (existing ones are left alone)
        # The first administrator, from ADMIN_EMAIL and ADMIN_PASSWORD in .env (only if there is none yet).
        admin.create_first_admin(os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD"))

    # Each page file is a Blueprint: a group of pages. Register them all with the app.
    app.register_blueprint(auth.bp)
    app.register_blueprint(account.bp)
    app.register_blueprint(stocks.bp)
    app.register_blueprint(trade.bp)
    app.register_blueprint(transfer.bp)
    app.register_blueprint(history.bp)
    app.register_blueprint(admin.bp)

    # Lets templates write {{ amount | money }} and {{ quantity | shares }}.
    app.add_template_filter(common.money, "money")
    app.add_template_filter(common.shares, "shares")

    # The friendly error pages.
    def show_error_page(error):
        status_code = getattr(error, "code", 500)
        if isinstance(error, CSRFError):
            status_code = 400
        title, message = ERROR_PAGES[status_code]
        html = render_template("errors/error.html", status=status_code, title=title, message=message)
        return html, status_code

    for status_code in ERROR_PAGES:
        app.register_error_handler(status_code, show_error_page)

    @app.get("/health")
    def health():
        """For checking the site is up: {"status": "ok"} if the app and the database both answer."""
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            return jsonify(status="error", database="unavailable"), 503
        return jsonify(status="ok", database="ok")

    return app
