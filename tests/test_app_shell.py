"""App shell and security baseline (task 1.5; N1.1, N2.3, N3.4, R1.11)."""
import hashlib
import re
from pathlib import Path

import pytest
from flask import abort, request, session
from flask_wtf.csrf import generate_csrf

from app import create_app
from app import errors
from app.auth import public
from app.security import CONTENT_SECURITY_POLICY
from tests.conftest import STRONG_SECRET_KEY

APP_DIR = Path(__file__).resolve().parent.parent / "app"

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def make_app(env, **config):
    app = create_app(env)
    app.config.update(config)
    return app


@pytest.fixture
def app(valid_env):
    return make_app(valid_env)


@pytest.fixture
def prod_app(valid_env):
    valid_env["APP_ENV"] = "production"
    valid_env["SECRET_KEY"] = STRONG_SECRET_KEY  # production refuses the test suite's weak key (task 6.1)
    return make_app(valid_env)


# --- Security headers (N1.1) ---------------------------------------------------

@pytest.mark.parametrize("path", ["/health", "/no-such-page", "/static/css/app.css"])
def test_security_headers_on_every_kind_of_response(app, path):
    response = app.test_client().get(path)
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name


def test_csp_forbids_inline_scripts_and_foreign_sources():
    assert "'unsafe-inline'" not in CONTENT_SECURITY_POLICY
    assert "'unsafe-eval'" not in CONTENT_SECURITY_POLICY
    assert "script-src 'self'" in CONTENT_SECURITY_POLICY
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY


def test_hsts_only_in_production(app, prod_app):
    assert "Strict-Transport-Security" not in app.test_client().get("/health").headers
    assert prod_app.test_client().get("/health").headers["Strict-Transport-Security"].startswith("max-age=")


def test_no_template_contains_inline_script_or_event_handlers():
    """CSP blocks inline scripts, so none may be written (they would silently not run)."""
    for template in (APP_DIR / "templates").rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        for match in re.finditer(r"<script\b([^>]*)>(.*?)</script>", text, re.S | re.I):
            attrs, body = match.groups()
            assert "src=" in attrs or 'type="application/json"' in attrs, f"inline script in {template.name}"
            assert 'type="application/json"' in attrs or not body.strip(), f"inline script in {template.name}"
        assert not re.search(r"\son[a-z]+\s*=", text, re.I), f"inline event handler in {template.name}"


# --- Relative sizing (D9) -------------------------------------------------------------

def our_css_and_templates():
    for path in [*(APP_DIR / "static" / "css").glob("*.css"), *(APP_DIR / "templates").rglob("*.html")]:
        yield path, path.read_text(encoding="utf-8")


def test_no_fixed_pixel_sizes_in_our_styles_or_templates():
    """Sizes must be relative so the site can adapt to any screen (D9). Vendored libraries are exempt."""
    for path, text in our_css_and_templates():
        assert not re.search(r"\d(\.\d+)?px\b", text), f"pixel size in {path.name}; use rem, %, or a Bootstrap variable"


def test_no_inline_styles_or_fixed_width_attributes():
    """Layout lives in CSS classes (Bootstrap grid or app.css), not style="" or width=/height= attributes."""
    for path, text in our_css_and_templates():
        if path.suffix == ".html":
            assert not re.search(r"\sstyle\s*=", text), f"inline style in {path.name}"
            assert not re.search(r"\s(width|height)\s*=\s*[\"']?\d", text), f"fixed size attribute in {path.name}"


def test_pages_declare_a_responsive_viewport(app):
    html = app.test_client().get("/no-such-page").get_data(as_text=True)
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html


# --- CSRF (N2.3) -----------------------------------------------------------------

def add_form_routes(app):
    @app.get("/test-form")
    @public
    def form_page():
        return generate_csrf()

    @app.post("/test-form")

    @public
    def form_post():
        return "saved"


def test_post_without_csrf_token_is_rejected_with_the_400_page(app):
    add_form_routes(app)
    response = app.test_client().post("/test-form", data={"x": "1"})
    assert response.status_code == 400
    assert b"Your form expired or was submitted incorrectly" in response.data


def test_post_with_a_forged_token_is_rejected(app):
    add_form_routes(app)
    client = app.test_client()
    client.get("/test-form")
    assert client.post("/test-form", data={"csrf_token": "forged"}).status_code == 400


def test_post_with_a_valid_token_is_accepted(app):
    add_form_routes(app)
    client = app.test_client()
    token = client.get("/test-form").get_data(as_text=True)
    response = client.post("/test-form", data={"csrf_token": token})
    assert response.status_code == 200 and response.data == b"saved"


# --- Error pages (N3.4, R2.4) ------------------------------------------------------

@pytest.mark.parametrize("status", sorted(errors.ERROR_PAGES))
def test_each_error_status_renders_the_custom_page(app, status):
    @app.get("/test-abort")
    @public
    def boom():
        abort(status)

    response = app.test_client().get("/test-abort")
    title, message = errors.ERROR_PAGES[status]
    body = response.get_data(as_text=True)
    assert response.status_code == status
    assert title in body and message.replace("'", "&#39;") in body
    assert "vendor/bootstrap/bootstrap.min.css" in body  # uses the site layout


def test_unknown_url_shows_the_custom_404(app):
    response = app.test_client().get("/no-such-page")
    assert response.status_code == 404
    assert b"Page not found" in response.data


def test_unexpected_exception_shows_500_without_internal_details(app):
    app.config["PROPAGATE_EXCEPTIONS"] = False  # behave like production, not the test runner

    @app.get("/test-crash")

    @public
    def crash():
        raise RuntimeError("secret-internal-detail: SELECT password_hash FROM `User`")

    response = app.test_client().get("/test-crash")
    body = response.get_data(as_text=True)
    assert response.status_code == 500
    assert "Something went wrong. Please try again." in body
    for leak in ("secret-internal-detail", "SELECT", "password_hash", "Traceback", "RuntimeError"):
        assert leak not in body


def test_not_found_domain_error_renders_the_404_page(app):  # R2.5
    @app.get("/test-not-yours")
    @public
    def not_yours():
        raise errors.NotFound()

    response = app.test_client().get("/test-not-yours")
    assert response.status_code == 404 and b"Page not found" in response.data


# --- Domain error messages (exact wording from design "Error handling") -------------

@pytest.mark.parametrize(
    "error, message",
    [
        (errors.InvalidCredentials(), "Invalid email or password"),                     # R1.4
        (errors.AccountLocked(15), "Too many failed attempts. Try again in 15 minutes."),  # R1.8
        (errors.MarketClosed(), "Market is closed"),                                    # R8.4
        (errors.InsufficientFunds(), "Insufficient funds"),                             # R8.5, R12.3
        (errors.InsufficientShares(), "Quantity exceeds shares owned"),                 # R9.4
        (errors.SymbolNotFound(), "Symbol not found"),                                  # R8.2
        (errors.DuplicateSymbol(), "Symbol already exists"),                            # R14.4
        (errors.NoBankAccount(), "Link a bank account before transferring funds"),      # R11.6
        (errors.OrderExpired(), "This order has expired. Please review it again."),     # R8.6
    ],
)
def test_domain_error_messages_match_the_spec(error, message):
    assert error.message == message == str(error)
    assert isinstance(error, errors.DomainError)


def test_validation_failed_carries_the_field():
    error = errors.ValidationFailed("amount", "Enter an amount greater than $0.00.")
    assert error.field == "amount" and error.message == "Enter an amount greater than $0.00."


# --- Cookies (R1.11) and proxy handling ----------------------------------------------

def add_session_route(app):
    @app.get("/test-session")
    @public
    def set_session():
        session["x"] = 1
        return "ok"


def test_session_cookie_is_secure_httponly_samesite_in_production(prod_app):
    add_session_route(prod_app)
    cookie = prod_app.test_client().get("/test-session").headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_session_cookie_is_httponly_samesite_in_development(app):
    add_session_route(app)
    cookie = app.test_client().get("/test-session").headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Secure" not in cookie  # local dev runs on plain http://127.0.0.1


def test_remember_cookie_settings(app, prod_app):  # R1.5, A2 (used by Flask-Login in task 2.1)
    assert app.config["REMEMBER_COOKIE_DURATION"].days == 30
    assert prod_app.config["REMEMBER_COOKIE_SECURE"] is True
    assert prod_app.config["REMEMBER_COOKIE_HTTPONLY"] is True


def add_scheme_route(app):
    @app.get("/test-scheme")
    @public
    def scheme():
        return f"{request.scheme} {request.remote_addr}"


def test_forwarded_headers_are_trusted_only_in_production(app, prod_app):
    headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9"}
    add_scheme_route(prod_app)
    add_scheme_route(app)
    assert prod_app.test_client().get("/test-scheme", headers=headers).data == b"https 203.0.113.9"
    assert app.test_client().get("/test-scheme", headers=headers).data == b"http 127.0.0.1"


# --- Vendored front-end files --------------------------------------------------------

def test_vendored_files_match_the_recorded_hashes(app):
    """static/vendor files must be exactly the versions listed in VERSIONS.md."""
    versions = (APP_DIR / "static" / "vendor" / "VERSIONS.md").read_text(encoding="utf-8")
    rows = re.findall(r"\| `([^`]+)` \| [\d.]+ \| \S+ \| `([0-9a-f]{64})` \|", versions)
    assert len(rows) == 3
    client = app.test_client()
    for path, expected in rows:
        response = client.get(f"/static/vendor/{path}")
        assert response.status_code == 200, path
        assert hashlib.sha256(response.data).hexdigest() == expected, path
