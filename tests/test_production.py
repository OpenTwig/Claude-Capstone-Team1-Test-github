"""Production configuration and hardening (task 6.1; R1.11, N1, N3.1, N3.4, N3.6; D28).

With APP_ENV=production the app refuses to start when debug is on, the cookie flags aren't secure, the
SECRET_KEY is missing or weak, the database user is root, or the process runs as root. The deployment
files (Gunicorn settings and systemd services for a Proxmox LXC behind a Cloudflare Tunnel) are checked
too, and logs never write sensitive values.
"""
import configparser
import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest

from app import create_app
from app.config import ConfigError, load_settings, production_problems
from app.logs import REDACTED, RedactFilter
from app.security import production_hardening_problems
from tests.conftest import STRONG_SECRET_KEY

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "deploy"


@pytest.fixture
def prod_env(valid_env):
    return dict(valid_env, APP_ENV="production", SECRET_KEY=STRONG_SECRET_KEY)


# --- Start-up guards ----------------------------------------------------------------------------------------

def test_a_well_configured_production_app_starts(prod_env):
    app = create_app(prod_env)
    assert app.config["SETTINGS"].is_production and not app.debug
    assert production_hardening_problems(app) == []


@pytest.mark.parametrize("key, reason", [
    ("short-but-random-Xy7", "too short"),
    ("a" * 64, "not random: one repeated character"),
    ("abababababababababababababababab", "not random: two characters"),
    ("test-secret-key-not-for-real-use", "the key the test suite uses"),
    ("changeme", "a placeholder"),
])
def test_a_weak_secret_key_is_refused_in_production(prod_env, key, reason):  # N1.4
    prod_env["SECRET_KEY"] = key
    with pytest.raises(ConfigError, match="SECRET_KEY is too weak for production"):
        load_settings(prod_env)


def test_a_missing_secret_key_is_refused(prod_env):  # N1.4
    del prod_env["SECRET_KEY"]
    with pytest.raises(ConfigError, match="missing required setting SECRET_KEY"):
        load_settings(prod_env)


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
def test_debug_mode_is_refused_in_production(prod_env, value):  # N3.4
    prod_env["FLASK_DEBUG"] = value
    with pytest.raises(ConfigError, match="debug mode must be off in production"):
        load_settings(prod_env)


def test_debug_off_is_fine(prod_env):
    prod_env["FLASK_DEBUG"] = "0"
    assert load_settings(prod_env).is_production


def test_the_root_database_account_is_refused_in_production(prod_env):  # N3.6
    prod_env["DB_USER"] = "root"
    with pytest.raises(ConfigError, match="DB_USER must not be root"):
        load_settings(prod_env)


def test_every_problem_is_listed_at_once(prod_env):
    prod_env.update(SECRET_KEY="changeme", FLASK_DEBUG="1", DB_USER="root")
    assert len(production_problems(prod_env)) == 3


@pytest.mark.parametrize("env_name", ["development", "test"])
def test_development_and_test_are_not_held_to_production_rules(valid_env, env_name):
    valid_env.update(APP_ENV=env_name, FLASK_DEBUG="1", DB_USER="root")
    assert load_settings(valid_env).app_env == env_name


def test_running_as_root_is_refused(prod_env, monkeypatch):  # N3.6
    import os

    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(ConfigError, match="running as root"):
        create_app(prod_env)


@pytest.mark.parametrize("flag", ["SESSION_COOKIE_SECURE", "SESSION_COOKIE_HTTPONLY", "REMEMBER_COOKIE_SECURE",
                                  "REMEMBER_COOKIE_HTTPONLY"])
def test_insecure_cookie_flags_are_caught(prod_env, flag):  # R1.11
    app = create_app(prod_env)
    app.config[flag] = False
    assert any(flag in p for p in production_hardening_problems(app))


def test_debug_on_the_built_app_is_caught(prod_env):  # N3.4
    app = create_app(prod_env)
    app.debug = True
    assert any("debug mode is on" in p for p in production_hardening_problems(app))


# --- No server software or version in responses ---------------------------------------------------------

def test_production_responses_do_not_reveal_the_server_software(prod_env):
    response = create_app(prod_env).test_client().get("/health")
    server = response.headers.get("Server", "")
    for giveaway in ("Werkzeug", "Python", "gunicorn", "Flask"):
        assert giveaway.lower() not in server.lower()
    assert "X-Powered-By" not in response.headers


def load_gunicorn_config(monkeypatch):
    """Run deploy/gunicorn.conf.py as Gunicorn would, with a stand-in 'gunicorn' module on Windows."""
    fake = types.ModuleType("gunicorn")
    fake.SERVER = "gunicorn/26.2.0"
    monkeypatch.setitem(sys.modules, "gunicorn", sys.modules.get("gunicorn", fake))
    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", DEPLOY / "gunicorn.conf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, sys.modules["gunicorn"]


def test_gunicorn_hides_its_version(monkeypatch):
    _, gunicorn = load_gunicorn_config(monkeypatch)
    assert gunicorn.SERVER == "webserver"


def test_gunicorn_listens_on_loopback_only(monkeypatch):  # D28: cloudflared is the only way in
    monkeypatch.delenv("GUNICORN_BIND", raising=False)
    conf, _ = load_gunicorn_config(monkeypatch)
    assert conf.bind == "127.0.0.1:8000"
    assert conf.workers >= 2 and conf.timeout == 30 and conf.accesslog == "-"
    assert "%(q)s" not in conf.access_log_format and "{" in conf.access_log_format   # no query strings logged


def test_gunicorn_bind_can_be_set_for_a_tunnel_on_another_vm(monkeypatch):  # D28, layout B in docs/deployment.md
    monkeypatch.setenv("GUNICORN_BIND", "10.1.0.14:8000")
    conf, _ = load_gunicorn_config(monkeypatch)
    assert conf.bind == "10.1.0.14:8000"


def test_gunicorn_refuses_to_start_as_root(monkeypatch):  # N3.6
    import os

    conf, _ = load_gunicorn_config(monkeypatch)
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(RuntimeError, match="as root"):
        conf.on_starting(None)
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    conf.on_starting(None)


# --- systemd services ---------------------------------------------------------------------------------------

def unit(name):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(DEPLOY / "systemd" / name, encoding="utf-8")
    return parser


@pytest.mark.parametrize("name, start", [
    # --no-control-socket: found on the first deploy, the control socket can't be made without a home folder.
    ("trading-web.service", '/opt/trading/.venv/bin/gunicorn -c deploy/gunicorn.conf.py --no-control-socket "app:create_app()"'),
    ("trading-price-generator.service", "/opt/trading/.venv/bin/flask --app app price-generator"),
])
def test_services_run_as_a_hardened_non_root_user(name, start):  # N3.6
    service = unit(name)["Service"]
    assert service["User"] == "trading" and service["Group"] == "trading"
    assert service["ExecStart"] == start
    assert service["EnvironmentFile"] == "/etc/trading/trading.env"   # secrets stay out of the unit file
    for setting in ("NoNewPrivileges", "ProtectHome", "PrivateTmp"):
        assert service[setting] == "yes", setting
    assert service["ProtectSystem"] == "strict" and service["Restart"] in ("on-failure", "always")
    text = (DEPLOY / "systemd" / name).read_text(encoding="utf-8")
    for secret in ("SECRET_KEY=", "DB_PASSWORD=", "FIELD_ENCRYPTION_KEYS="):
        assert secret not in text


def test_cloudflared_sends_the_site_to_loopback_and_refuses_the_rest():  # D28
    text = (DEPLOY / "cloudflared" / "config.yml.example").read_text(encoding="utf-8")
    assert "service: http://127.0.0.1:8000" in text and "service: http_status:404" in text


# --- Logs never write sensitive values (N1.3) ---------------------------------------------------------------

@pytest.mark.parametrize("message, args", [
    ("SSN %s on file", ("123-45-6789",)),
    ("SSN 123456789 on file", ()),
    ("account %s linked", ("000123456789",)),
    ("account 12345678901234567 linked", ()),
])
def test_the_redact_filter_blanks_sensitive_shapes(message, args):
    record = logging.LogRecord("app", logging.INFO, __file__, 1, message, args, None)
    RedactFilter().filter(record)
    text = record.getMessage()
    assert REDACTED in text and not any(ch.isdigit() for ch in text.replace(REDACTED, ""))


def test_the_redact_filter_leaves_ordinary_numbers(caplog):
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "portfolio %s moved $%s at %s", (42, "1,000.00", "10:05"), None)
    RedactFilter().filter(record)
    assert record.getMessage() == "portfolio 42 moved $1,000.00 at 10:05"


def test_the_redact_filter_cleans_tracebacks():
    try:
        raise ValueError("bad SSN 123-45-6789")
    except ValueError:
        record = logging.LogRecord("app", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    RedactFilter().filter(record)
    assert "123-45-6789" not in record.exc_text and REDACTED in record.exc_text


def test_production_logging_redacts_on_every_handler(prod_env):
    create_app(prod_env)
    root = logging.getLogger()
    assert root.handlers and all(any(isinstance(f, RedactFilter) for f in h.filters) for h in root.handlers)


@pytest.mark.db
def test_nothing_sensitive_is_logged(db, web_app, fixed_clock, caplog):  # N1.3: the first line of defence
    """Every log record, at every level, from the sensitive flows: none contains a password, an SSN, an account
    number, or a temporary password. (caplog sees records before any filter, so this checks what the code logs.)"""
    import re
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from tests.web import csrf_token, login, make_login_user

    fixed_clock(datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("America/Phoenix")))
    caplog.set_level(logging.DEBUG)
    # The redaction filter is the second line of defence. It rewrites the log record itself, which every handler
    # shares, so take it off every handler: this test must see exactly what the code logs, not a cleaned-up copy.
    for handler in logging.getLogger().handlers + [caplog.handler]:
        for found in [f for f in handler.filters if isinstance(f, RedactFilter)]:
            handler.removeFilter(found)
    make_login_user(db, "correct horse battery", admin=True, email="admin@example.test")
    admin = web_app.test_client()
    login(admin, "admin@example.test", "wrong password here")                 # a failed login
    login(admin, "admin@example.test", "correct horse battery")
    page = admin.post("/admin/users", data={"name": "Casey", "ssn": "987-65-4329", "email": "casey@example.test",
                                            "date_of_birth": "01/15/1990", "role": "Customer",
                                            "csrf_token": csrf_token(admin, "/admin")}).get_data(as_text=True)
    temporary = re.search(r"data-temp-password>([^<]+)<", page).group(1)
    with db.cursor() as cur:
        cur.execute("SELECT userID FROM `User` WHERE email = 'casey@example.test'")
        casey = cur.fetchone()["userID"]
    db.commit()
    reissued = re.search(r"data-temp-password>([^<]+)<", admin.post(
        f"/admin/users/{casey}/temporary-password", data={"csrf_token": csrf_token(admin, "/admin")}).get_data(as_text=True)).group(1)
    customer = web_app.test_client()
    login(customer, "casey@example.test", reissued)
    customer.post("/change-password", data={"new_password": "Jumping-Otter-42", "confirm_password": "Jumping-Otter-42",
                                            "csrf_token": csrf_token(customer, "/change-password")})
    customer.post("/account/portfolios", data={"account_name": "Main", "account_type": "Brokerage",
                                               "csrf_token": csrf_token(customer, "/account")})
    customer.post("/transfer/bank-accounts", data={"bank_name": "First Bank", "account_number": "000555444333",
                                                   "routing_number": "021000021", "nickname": "Checking",
                                                   "csrf_token": csrf_token(customer, "/transfer")})
    logged = caplog.text + "".join(str(r.args) for r in caplog.records)
    for secret in ("wrong password here", "correct horse battery", "987-65-4329", "987654329", temporary, reissued,
                   "Jumping-Otter-42", "000555444333"):
        assert secret not in logged, f"a log record contains {secret[:4]}…"


def test_production_never_reads_a_dotenv_file(monkeypatch, tmp_path, prod_env):  # docs/deployment.md, step 7
    """With APP_ENV=production in the environment (as systemd sets it), a stray .env in the working directory is
    ignored, even an unreadable one; settings come only from the environment."""
    import app as app_package

    for key, value in prod_env.items():
        monkeypatch.setenv(key, value)
    (tmp_path / ".env").write_text("SECRET_KEY=from-a-stray-dotenv-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_package, "find_dotenv", lambda **kw: pytest.fail("a .env file was looked for in production"))
    built = create_app()
    assert built.config["SECRET_KEY"] == STRONG_SECRET_KEY


def test_development_still_reads_its_dotenv_file(monkeypatch, tmp_path, valid_env):
    import app as app_package

    for key, value in valid_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    (tmp_path / ".env").write_text("SUPPORT_EMAIL=help@example.test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    seen = []
    real = app_package.find_dotenv
    monkeypatch.setattr(app_package, "find_dotenv", lambda **kw: seen.append(1) or real(**kw))
    assert create_app().config["SETTINGS"].support_email == "help@example.test" and seen


def test_services_skip_flasks_own_dotenv_loading():  # the generator runs through the flask command
    for name in ("trading-web.service", "trading-price-generator.service"):
        assert "Environment=FLASK_SKIP_DOTENV=1" in (DEPLOY / "systemd" / name).read_text(encoding="utf-8"), name
