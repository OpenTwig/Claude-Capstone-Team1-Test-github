"""Security baseline: CSRF, cookies, headers, proxy handling, rate limiting (design: Security design)."""
from __future__ import annotations

from datetime import timedelta

from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Settings

csrf = CSRFProtect()

# In-memory storage is per server process; the database lockout (A1) is the cross-server control.
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",          # no inline scripts
        "style-src 'self'",
        "img-src 'self' data:",       # Bootstrap's CSS uses data: SVG icons
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


def _add_security_headers(response, production: bool):
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def init_security(app: Flask, settings: Settings) -> None:
    production = settings.is_production
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=production,
        REMEMBER_COOKIE_DURATION=timedelta(days=settings.remember_days),  # A2
    )
    csrf.init_app(app)
    limiter.init_app(app)
    if production:
        # HTTPS ends at Cloudflare; cloudflared passes X-Forwarded-For/-Proto to Gunicorn on 127.0.0.1
        # (one proxy hop, D28), so the rate limit sees each visitor's address and url_for makes https links.
        # Not enabled elsewhere, because without a proxy clients could spoof these headers.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.after_request(lambda response: _add_security_headers(response, production))


def production_hardening_problems(app: Flask) -> list[str]:
    """Checks on the built app, for APP_ENV=production (task 6.1; R1.11, N3.4, N3.6)."""
    import os

    problems = []
    if app.debug:
        problems.append("debug mode is on (the debugger allows running code): unset FLASK_DEBUG")
    for name in ("SESSION_COOKIE_SECURE", "SESSION_COOKIE_HTTPONLY", "REMEMBER_COOKIE_SECURE", "REMEMBER_COOKIE_HTTPONLY"):
        if not app.config.get(name):
            problems.append(f"{name} must be on in production")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        problems.append("the app is running as root: run it as the dedicated non-root 'trading' user")
    return problems
