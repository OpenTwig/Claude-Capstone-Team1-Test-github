"""Application factory and startup (task 0.1)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import create_app
from app.config import REQUIRED, ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_app_builds_from_explicit_settings(valid_env):
    app = create_app(valid_env)
    assert app.config["SECRET_KEY"] == valid_env["SECRET_KEY"]
    assert app.config["SETTINGS"].db_name == "trading_test"


def test_test_environment_turns_on_testing_mode(valid_env):
    assert create_app(valid_env).config["TESTING"] is True
    valid_env["APP_ENV"] = "development"
    assert create_app(valid_env).config["TESTING"] is False


@pytest.mark.db
def test_health_endpoint_responds(db_env):  # task 6.2: the app and its database both answer
    response = create_app(db_env).test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "database": "ok"}


def test_health_reports_an_unreachable_database_without_details(valid_env, caplog):  # task 6.2, N3.4
    valid_env.update(DB_HOST="127.0.0.1", DB_PORT="1")   # nothing listens on port 1: refused at once
    response = create_app(valid_env).test_client().get("/health")
    assert response.status_code == 503
    assert response.get_json() == {"status": "error", "database": "unavailable"}
    body = response.get_data(as_text=True)
    for detail in ("127.0.0.1", "trading_app", "Can't connect", "2003", "Traceback"):
        assert detail not in body
    assert "health check: the database did not answer (MySQL error 2003)" in caplog.text


def test_create_app_fails_fast_without_secret_key(valid_env):  # N1.4
    del valid_env["SECRET_KEY"]
    with pytest.raises(ConfigError, match="missing required setting SECRET_KEY"):
        create_app(valid_env)


def test_create_app_reads_process_environment_when_no_settings_given(valid_env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here, so only the process environment is used
    for key, value in valid_env.items():
        monkeypatch.setenv(key, value)
    assert create_app().config["SECRET_KEY"] == valid_env["SECRET_KEY"]


def test_flask_cli_refuses_to_start_without_secret_key(valid_env, tmp_path):  # N1.4
    """Start the real `flask` command the way a developer would, minus SECRET_KEY."""
    env = {k: v for k, v in os.environ.items() if k not in REQUIRED and not k.startswith(("DB_", "APP_ENV"))}
    env.update(valid_env)
    del env["SECRET_KEY"]
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "flask", "--app", "app", "routes"],
        cwd=tmp_path,  # outside the repo, so no .env file is picked up
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "missing required setting SECRET_KEY" in result.stderr + result.stdout
