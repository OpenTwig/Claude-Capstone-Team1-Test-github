"""Flask CLI commands (design: Project layout, cli.py). More commands arrive in later tasks."""
from __future__ import annotations

import click
from flask import Flask, current_app
from flask.cli import with_appcontext

from app.db import connect
from app.migrate import grant_app_privileges, upgrade


@click.command("db-upgrade")
@with_appcontext
def db_upgrade_command() -> None:
    """Apply new migrations, then (re)apply the app account's table grants."""
    settings = current_app.config["SETTINGS"]
    if not settings.db_migrate_user or not settings.db_migrate_password:
        raise click.ClickException("DB_MIGRATE_USER and DB_MIGRATE_PASSWORD must be set to run migrations.")
    conn = connect(settings, user=settings.db_migrate_user, password=settings.db_migrate_password)
    try:
        applied = upgrade(conn)
        grant_app_privileges(conn, settings.db_name, settings.db_user)
    finally:
        conn.close()
    if applied:
        click.echo(f"Applied {len(applied)} migration(s) to {settings.db_name}: {', '.join(applied)}")
    else:
        click.echo(f"No new migrations for {settings.db_name}.")
    click.echo(f"Table privileges for {settings.db_user} are up to date.")


@click.command("create-admin")
@with_appcontext
def create_admin_command() -> None:
    """Create an administrator account (bootstraps the first admin; design: User administration)."""
    from app.errors import ValidationFailed
    from app.services import users, validation

    name = click.prompt("Full name")
    email = click.prompt("Email")
    dob_text = click.prompt("Date of birth (MM/DD/YYYY)")
    ssn = click.prompt("SSN (XXX-XX-XXXX, hidden)", hide_input=True)
    password = click.prompt(
        f"Password (at least {users.MIN_ADMIN_PASSWORD_LENGTH} characters, hidden)",
        hide_input=True,
        confirmation_prompt=True,
    )
    access_level = click.prompt("Access level", default="admin")
    department = click.prompt("Department (optional)", default="", show_default=False)
    try:
        dob = validation.us_date(dob_text, "date_of_birth", "the date of birth")
        user_id = users.create_admin(name, ssn, email, dob, password, access_level, department)
    except ValidationFailed as exc:
        raise click.ClickException(exc.message) from exc
    click.echo(f"Administrator created (user ID {user_id}). Log in at /signin with {email.strip().lower()}.")


@click.command("issue-temp-password")
@click.argument("email")
@with_appcontext
def issue_temp_password_command(email: str) -> None:
    """Give a user a new temporary password (R18.10), e.g. when the only administrator is locked out."""
    from app.errors import NotFound
    from app.services import users

    try:
        temporary_password = users.issue_temporary_password_by_email(email)
    except NotFound as exc:
        raise click.ClickException(exc.message) from exc
    days = current_app.config["SETTINGS"].temp_password_days
    click.echo(f"New temporary password for {email.strip().lower()} (shown once; expires in {days} days):")
    click.echo(temporary_password)
    click.echo("They must choose a new password when they log in. Their existing logins have been ended.")


@click.command("price-generator")
@with_appcontext
def price_generator_command() -> None:
    """Run the random price generator until stopped with Ctrl+C (R16, DD6)."""
    from app.services import price_generator

    click.echo("Price generator running. Press Ctrl+C to stop.")
    try:
        if not price_generator.run(current_app._get_current_object()):
            raise click.ClickException("Another price generator is already running for this database.")
    except KeyboardInterrupt:
        click.echo("Price generator stopped.")


@click.command("seed")
@with_appcontext
def seed_command() -> None:
    """Add sample stocks and a demo customer for local development (task 4.6). Safe to run again."""
    from app.seed import SeedRefused, run_seed

    settings = current_app.config["SETTINGS"]
    try:
        result = run_seed(settings)
    except SeedRefused as exc:
        raise click.ClickException(str(exc)) from exc
    if result.stocks_added:
        click.echo(f"Added {len(result.stocks_added)} sample stock(s) with a year of price history: "
                   f"{', '.join(result.stocks_added)}")
    if result.stocks_skipped:
        click.echo(f"Already there, skipped: {', '.join(result.stocks_skipped)}")
    if result.demo_password:
        click.echo(f"Demo customer {result.demo_email} created with a Brokerage portfolio and a deposit.")
        click.echo("Temporary password (shown once; they must choose a new one at first login):")
        click.echo(result.demo_password)
    else:
        click.echo(f"Demo customer {result.demo_email} already exists, skipped.")


def init_cli(app: Flask) -> None:
    app.cli.add_command(db_upgrade_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(issue_temp_password_command)
    app.cli.add_command(price_generator_command)
    app.cli.add_command(seed_command)
