"""User-facing errors and HTTP error pages (design: Error handling).

Services raise DomainError subclasses carrying the exact message shown to the
user. Routes catch DomainError and display its message; NotFound is rendered
as the 404 page. Unexpected exceptions become the 500 page, which never shows
internal details (N3.4).
"""
from __future__ import annotations

from flask import Flask, render_template
from flask_wtf.csrf import CSRFError


class DomainError(Exception):
    """A business-rule violation with a message safe to show the user."""

    message = "Something went wrong. Please try again."

    def __init__(self, message: str | None = None):
        self.message = message or type(self).message
        super().__init__(self.message)


class InvalidCredentials(DomainError):
    message = "Invalid email or password"  # R1.4, R3.11


class AccountLocked(DomainError):
    message = "Too many failed attempts. Try again in 15 minutes."  # R1.8

    def __init__(self, minutes: int = 15):
        super().__init__(f"Too many failed attempts. Try again in {minutes} minutes.")


class TemporaryPasswordExpired(DomainError):
    message = "Your temporary password has expired. Ask an administrator for a new one."  # R18.8


class MarketClosed(DomainError):
    message = "Market is closed"  # R8.4, R9.3


class InsufficientFunds(DomainError):
    message = "Insufficient funds"  # R8.5, R12.3


class InsufficientShares(DomainError):
    message = "Quantity exceeds shares owned"  # R9.4


class SymbolNotFound(DomainError):
    message = "Symbol not found"  # R8.2


class DuplicateSymbol(DomainError):
    message = "Symbol already exists"  # R14.4


class NotEnoughSharesAvailable(DomainError):
    message = "Not enough shares available"  # R8.11, D16


class NoBankAccount(DomainError):
    message = "Link a bank account before transferring funds"  # R11.6


class OrderExpired(DomainError):
    message = "This order has expired. Please review it again."  # R8.6


class ValidationFailed(DomainError):
    """A single form field is invalid."""

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(message)


class NotFound(DomainError):
    """The record does not exist, or belongs to someone else (R2.5). Rendered as the 404 page."""

    message = "Page not found"


class Forbidden(DomainError):
    """An administrator-only action was attempted by someone else (R2.4). Rendered as the 403 page."""

    message = "You don't have permission to do that."


ERROR_PAGES = {
    400: ("Request not accepted", "Your form expired or was submitted incorrectly. Go back, refresh the page, and try again."),
    403: ("Access denied", "You don't have permission to view this page."),  # R2.4
    404: ("Page not found", "The page you asked for doesn't exist."),
    405: ("Not allowed", "That action isn't allowed here."),
    429: ("Too many requests", "Too many attempts. Please wait a moment and try again."),  # R1.9
    500: ("Something went wrong", "Something went wrong. Please try again."),  # N3.4
}


def render_error(status: int):
    title, message = ERROR_PAGES[status]
    return render_template("errors/error.html", status=status, title=title, message=message), status


def register_error_handlers(app: Flask) -> None:
    for status in ERROR_PAGES:
        app.register_error_handler(status, lambda exc, status=status: render_error(status))
    app.register_error_handler(CSRFError, lambda exc: render_error(400))
    app.register_error_handler(NotFound, lambda exc: render_error(404))
    app.register_error_handler(Forbidden, lambda exc: render_error(403))
