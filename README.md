# Stock Trading System

Team 1 capstone: a Flask and MySQL web app where customers fund portfolios and trade stocks at simulated prices, and administrators manage users, stocks, and market hours.

## Running it

Needs Python 3.11+ and MySQL 8 or MariaDB 10.5+.

1. Create the database and an account for the app (in the `mysql` or `mariadb` client, as an admin user):

   ```sql
   CREATE DATABASE trading_db;
   CREATE USER 'trading_app'@'localhost' IDENTIFIED BY 'choose-a-password';
   GRANT ALL ON trading_db.* TO 'trading_app'@'localhost';
   ```

2. Install and configure:

   Linux/macOS:

   ```bash
   python -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   cp .env.example .env        # then fill it in (it explains how to generate the keys)
   ```

   Windows:
   
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   Copy-Item .env.example .env        # then fill it in (it explains how to generate the keys)
   ```

   Put the first administrator's email and password in `.env` as `ADMIN_EMAIL` and `ADMIN_PASSWORD`.
   The first time the site starts, it creates the tables and that administrator. Log in as them to add
   stocks and customers on the Administration page.

4. Run the site at http://127.0.0.1:5000, and (in a second terminal) the price generator:

   Linux/macOS:

   ```bash
   .venv/bin/python -m flask --app app run --debug
   .venv/bin/python price_generator.py
   ```

   Windows:

   ```powershell
   .\.venv\Scripts\python.exe -m flask --app app run --debug
   .\.venv\Scripts\python.exe price_generator.py
   ```
   
## Code

Each page has one Python file in `app/` and its templates in `app/templates/<name>/`:
`auth` (login), `account` (Summary and Positions), `stocks`, `trade`, `transfer`, `history`, and `admin`.
`models.py` holds the database tables as Flask-SQLAlchemy models, `market.py` the market hours and the
price generator, `common.py` small shared helpers, and `holidays.json` the days the market is closed
(add each new year's dates there). `price_generator.py`, outside `app/`, starts the price generator.
Bootstrap and Chart.js load from the jsDelivr CDN; `static/` holds only the site's own CSS and JavaScript.

## Documentation

- [docs/](docs/): the original Systems Analysis Design and its diagrams
