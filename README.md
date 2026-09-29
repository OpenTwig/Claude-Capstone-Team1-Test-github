# Stock Trading System

Team 1 capstone: a Flask and MySQL web app where customers fund portfolios and trade stocks at simulated prices, and administrators manage users, stocks, and market hours.

## Documentation

- [docs/requirements.md](docs/requirements.md): what the system must do
- [docs/design.md](docs/design.md): how it is built
- [docs/tasks.md](docs/tasks.md): the build plan, progress, and definition of done
- [docs/deployment.md](docs/deployment.md): putting the site online (Proxmox LXC + Cloudflare Tunnel)
- [docs/test-report.md](docs/test-report.md): the Wave 5 verification results
- [docs/reference/](docs/reference/): the original Systems Analysis Design and its diagrams

## Quickstart (Windows, local development)

Requires Python 3.11+ and MySQL 8.0.19+ running on `localhost`. Full details: "Local development" in [docs/design.md](docs/design.md).

```powershell
# 1. Virtual environment: all installing, running, and testing happens inside it
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt

# 2. Settings and local databases (asks for your MySQL admin username and
#    password; creates .env from .env.example and fills in generated secrets)
python scripts\setup_local_db.py

# 3. Run the app on http://127.0.0.1:5000 and the tests
python -m flask --app app run --debug
python -m pytest
```

In VS Code, select `.venv\Scripts\python.exe` as the interpreter.
