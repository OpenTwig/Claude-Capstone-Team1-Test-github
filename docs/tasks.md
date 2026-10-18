# Implementation Tasks

**Project:** Stock Trading System (Team 1 capstone)
**Implements:** `docs/design.md`, which implements `docs/requirements.md`

## How to use this file

**Waves.** Tasks are grouped into waves. Every task in a wave depends only on tasks in earlier waves, never on another task in the same wave, so tasks within a wave can be done in any order, by different people or agents at the same time, or together by one person.

**Checkpoints.** Each wave ends with a checkpoint. The next wave starts only after that checkpoint has passed:

1. Every task in the wave is checked off.
2. The full test suite runs in the venv and passes: `.venv\Scripts\python -m pytest`.
3. The wave's manual checks (listed on the checkpoint) are done in a browser against the local dev server.
4. The results (the pytest summary line, anything that failed or was fixed, and any open concerns) are shown to the user, who approves moving on.
5. The checkpoint is checked with a line `  - **Passed:** <date> · <commit> · <test result> · approved by <name>`.

**Depends on.** Each task's **Depends on** line names the specific earlier tasks it builds on. The checkpoint is the gate; the Depends on line tells you what to re-test when an earlier task is reopened or reworked. The machine-readable graph at the end of this file is derived from these lines.

**Status.** Each task is a checkbox:

- `- [ ]` not done. If someone is working on it, add a line `  - **In progress:** <name>, <date>` under it so nobody duplicates the work.
- `- [x]` done **and verified** (see Definition of done), with a completion line: `  - **Done:** <date> · <commit> · <test result, e.g. "212 passed">`.

Anything unchecked is not finished. If a task is only partly done, leave it unchecked and add a **Remaining:** line saying exactly what is left.

**Blocked work.** Items that depend on an unanswered open question (Q#, see `docs/requirements.md`) are in the **Parked** section at the end, not in the waves. When a question is answered, move its item into the right wave as a normal task. A task built on a *proposed* answer is marked **Needs approval** until the team confirms the proposal (Q1 and Q3 were approved as D11 and D12).

### Definition of done

A task is checked off only when **all** of these are true. "It should work" is never enough.

1. The code and its tests are written. Tests cite requirement and property IDs in their names or comments (e.g. `test_buy_rejected_when_market_closed  # R8.4`).
2. The tests were **run inside the project venv** and passed, and the person or agent completing the task saw the output:
   - the task's own tests, plus the test files that cover any shared code the task changed (for example `app/db.py`, `app/services/market.py`, or `base.html`): `.venv\Scripts\python -m pytest <test paths> -v`
   - the full suite (`.venv\Scripts\python -m pytest`, about 5 minutes) runs **once per wave, at its checkpoint**, not after every task (decided by the user on 2026-09-28 to save time). A checkpoint isn't passed until the full suite passes; a regression it finds reopens the task that caused it.
3. For tasks that add or change a page: the page was opened in a browser against the local dev server and checked against its wireframe in `docs/reference/images/`, at a narrow, phone-like width (browser responsive mode) to confirm nothing is cut off or overlapping (D9; a full mobile design pass comes later), and in both dark and light mode (D13). **The task that creates a blueprint also registers it in `create_app()`**, and its route tests request the page through the app built by `create_app()`, so an unregistered route fails a test instead of returning 404 in the browser.
4. Every item in the task's **Tests** list exists and passes. If one was dropped, the task is not done.
5. The work is committed (summary + description, citing the task ID, e.g. "Task 3.2").
6. The box is checked with a **Done** line recording the date, commit, and test result.
7. Anything learned along the way that the next person would otherwise rediscover (an environment quirk, a contract between two pieces of code) is added to **Notes and lessons learned** at the end of this file.

If a test fails, the task stays open until the code is fixed. Tests are never deleted or weakened to make them pass.

### Wave overview

| Wave | Theme | Tasks | Can start after |
|---|---|---|---|
| 0 | Project setup | 0.1–0.2 | nothing |
| 1 | Foundations: schema, money, crypto, calendar, app shell, price math | 1.1–1.6 | Checkpoint 0 |
| 2 | Core services: accounts, portfolios, stocks, schedule | 2.1–2.4 | Checkpoint 1 |
| 3 | Money movement, trading, price generator, first-login password change, dark mode | 3.1–3.3, 3.5–3.6 | Checkpoint 2 |
| 4 | Pages and sample data | 4.1–4.6 | Checkpoint 3 |
| 5 | Verification: security, math, access control, concurrency, manual walkthrough | 5.1–5.6 | Checkpoint 4 |
| 6 | Production readiness; market days and manual open | 6.1–6.3 | Checkpoint 5 |
| Parked | Waiting on open questions | none | the question's answer |
| Future | Improvements to consider after the task list | none | Checkpoint 6 and the user's go-ahead |

---

## Wave 0: Project setup

- [x] **0.1 Scaffold the project and virtual environment**
  - **Done:** 2026-09-28 · b040f63 · task tests 30 passed; full suite 47 passed; dev server `GET /health` → 200
  - Create `.venv`; install the packages in design "Dependencies" plus pytest and hypothesis; write pinned `requirements.txt` and `requirements-dev.txt` from `pip freeze` (design "Version pinning", including the gunicorn Windows marker).
  - `app/__init__.py` with `create_app()`; `app/config.py` reading every variable in design "Configuration", failing fast with a clear message when a required one is missing; `APP_ENV` defaults to `production`.
  - `.env.example`, `.vscode/` (`extensions.json`, `settings.json`, `launch.json` from design "VS Code"), and a short `README.md` quickstart pointing to design "Local development".
  - Fill in the Commands section of `CLAUDE.md`.
  - **Tests:** app factory builds in test config; a smoke request returns a response; starting without `SECRET_KEY` fails with a clear error.
  - **Done when:** a fresh clone → create venv → `pip install -r requirements-dev.txt` → `.venv\Scripts\python -m pytest` passes.
  - **Depends on:** nothing · **Requirements:** N5 · **Design:** Project layout, Dependencies, Configuration, VS Code

- [x] **0.2 Local MySQL databases and accounts**
  - **Done:** 2026-09-28 · 8438401 · task tests 20 passed; full suite 50 passed; setup run by the user as `trading_admin`; `--check`: trading_migrate → trading_db OK, trading_migrate → trading_test OK, trading_app → server OK (MySQL 8.0.46)
  - `scripts/setup_local_db.py`, run by a developer (it asks for their MySQL admin username and password; the account need not be root, but needs CREATE, CREATE USER, and ALL WITH GRANT OPTION on the two databases): creates `.env` from `.env.example` and fills blank secrets; creates `trading_db` and `trading_test` (`utf8mb4`, `utf8mb4_0900_ai_ci`) and the `trading_migrate` and `trading_app` accounts at `localhost`; gives `trading_migrate` full rights on both databases only; then verifies connections. `--check` re-runs the verification without an admin login. `trading_app`'s table-level grants come in task 1.1, because the tables must exist first.
  - **Tests:** blank secrets are filled and existing values never overwritten; generated Fernet keys are valid and DB passwords are strong; unsafe database/account names are rejected; account names and passwords reach MySQL as parameters, never in the SQL text; `.env.example` lists every required setting and contains no secret values.
  - **Done when:** the script runs cleanly on MySQL 8.0.19 or later; `--check` shows `trading_migrate` connecting to both databases and `trading_app` authenticating to the server.
  - **Depends on:** nothing · **Requirements:** N3.6 · **Design:** Local development, Database accounts

- [x] **Checkpoint 0**
  - Manual: `python -m flask --app app run --debug` serves `http://127.0.0.1:5000/health`; `scripts\setup_local_db.py --check` passes.
  - **Passed:** 2026-09-28 · d053249 · 50 passed; `/health` → 200 with the real `.env`; `--check` all OK · approved by OpenTwig

---

## Wave 1: Foundations

- [x] **1.1 Schema, migrations, database access, and test harness**
  - **Done:** 2026-09-28 · c3522fe · task tests 80 passed; full suite 293 passed; `flask db-upgrade` built `trading_db` (second run: no changes); trading_app on trading_db can SELECT but is refused DELETE History, UPDATE Transaction, DROP TABLE (1142)
  - `migrations/001_initial_schema.sql`, exactly per design "Schema".
  - `app/db.py`: per-request connection, `transaction()` context manager, `SET time_zone = '+00:00'`.
  - `flask db-upgrade`: applies unapplied migration files in order as `trading_migrate`, records them in `schema_migrations`.
  - `trading_app` table-level grants (`APP_PRIVILEGES` in `app/migrate.py`), applied by `db-upgrade` after migrating, per design "Database accounts". Requires `trading_migrate` to have `GRANT OPTION` on the two databases (setup script).
  - Test harness in `tests/conftest.py`: migrate `trading_test` once per session; truncate tables before each test; fixtures for the app, test client, fixed clock, and seeded RNG; factories that insert rows directly (`make_user`, `make_admin`, `make_portfolio`, `make_bank_account`, `make_stock`); Hypothesis profiles `default` (100), `quick` (20), `thorough` (1000).
  - **Tests:**
    - migrations apply to an empty database, and running `db-upgrade` again changes nothing;
    - each constraint rejects bad data: negative cash, lowercase ticker, zero-quantity Inventory row, History with both or neither of transaction/transfer, Transfer with the wrong from/to/bank combination, duplicate `StockPrice` day, bad routing number;
    - connected as `trading_app`: `DELETE` on `Transaction`, `Transfer`, `History` and `UPDATE` on `Transaction`, `History` are denied (R17.2).
  - **Depends on:** 0.1, 0.2 · **Requirements:** N2.4, N3.6, N4, R17.2 · **Design:** Data model, Core modules, Testing strategy

- [x] **1.2 Money helpers** (`app/money.py`)
  - **Done:** 2026-09-28 · c3522fe · task tests 62 passed; full suite 293 passed
  - `to_cents`, `to_shares_floor`, `parse_money`, per design "Core modules".
  - **Tests:** a table of rounding cases (e.g. 0.005 → 0.01, 0.0049 → 0.00; share quantities never round up); `parse_money` rejects `""`, `"abc"`, `"-1"`, `"0"`, `"1.001"`, `"1e3"`, `"NaN"`, `"Infinity"`, and anything over 99,999,999.99; accepts `"1"`, `"1.5"`, `"99999999.99"`; results are always `Decimal`, never `float`.
  - **Depends on:** 0.1 · **Requirements:** N4.1, A3 · **Design:** Core modules (`money.py`)

- [x] **1.3 Field encryption and masking** (`app/crypto.py`)
  - **Done:** 2026-09-28 · c3522fe · task tests 24 passed; full suite 293 passed
  - `encrypt_field`, `decrypt_field` (MultiFernet), `mask_last4`.
  - **Tests:** round trip; ciphertext does not contain the plaintext; a value encrypted with an old key still decrypts after a new key is added; a wrong key raises an error; `mask_last4` output.
  - **Depends on:** 0.1 · **Requirements:** N1.3 · **Design:** Core modules (`crypto.py`), DD8

- [x] **1.4 Clock and market calendar** (`app/clock.py`, pure part of `services/market.py`)
  - **Done:** 2026-09-28 · c3522fe · task tests 27 passed; full suite 293 passed
  - `now_utc`, `now_local`; `is_open(at_utc, schedule)`, `is_holiday`, and next-open calculation, with the schedule passed in (the stored schedule comes in 2.4).
  - **Tests:** boundaries at 8:59:59.999, 9:00:00, 16:59:59.999, and 17:00:00 Arizona; Saturday and Sunday; 2026 NYSE holidays (e.g. Good Friday 2026-04-03, Independence Day observed 2026-07-03, Thanksgiving 2026-11-26); next open from Friday 17:00 is Monday 9:00; next open skips a holiday; UTC → Arizona conversion (16:00 UTC = 09:00 Arizona).
  - **Depends on:** 0.1 · **Requirements:** R15.1, R15.2, D4, D6, A16 · **Design:** Market clock

- [x] **1.5 App shell and security baseline**
  - **Done:** 2026-09-28 · c3522fe · task tests 33 passed; full suite 293 passed; running dev server: `/no-such-page` → custom 404 with CSP and X-Frame-Options headers
  - `errors.py` (DomainError hierarchy), `security.py` (CSRFProtect, security headers, ProxyFix, cookie settings, limiter setup), error pages (403/404/500), `base.html` layout, Bootstrap and Chart.js vendored into `static/vendor/`.
  - **Tests:** every response carries the security headers; a POST without a CSRF token gets 400; an unknown URL shows the custom 404; a forced exception shows the 500 page with no stack trace or SQL in the body; production config sets Secure/HttpOnly/SameSite cookie flags.
  - **Depends on:** 0.1 · **Requirements:** N1.1, N2.3, N3.4, R1.11 · **Design:** Security design, Error handling

- [x] **1.6 Price model math** (pure functions in `services/pricing.py`)
  - **Done:** 2026-09-28 · c3522fe · task tests 14 passed; full suite 293 passed
  - `next_price(exact, seconds_elapsed, trading_day_seconds, rng, cfg)` and `history_walk(price, trading_days, rng, cfg)`, per design "Price model".
  - **Tests (P6):** price never below $0.01; no tick beyond the cap (including with an absurd volatility setting); overnight gaps are capped at `TICK_MAX`; seeded 250-day runs give a daily standard deviation within 1.3–1.7% at $1, $5, and $150; the same seed gives the same output; outputs are `Decimal`.
  - **Depends on:** 0.1 · **Requirements:** R16.2, R16.3, A7, A9 · **Design:** Price generator (Price model)

- [x] **Checkpoint 1**
  - Manual: `flask db-upgrade` builds `trading_db` and applies `trading_app`'s grants; the 404 page renders in a browser with the site layout; response headers include the security headers (browser dev tools). The 500 page is checked by automated tests, since with `--debug` Flask deliberately shows its debugger instead.
  - **Passed:** 2026-09-28 · c3522fe · 293 passed; db-upgrade built trading_db; 404 page and security headers verified in Firefox by the user · approved by OpenTwig

---

## Wave 2: Core services

- [x] **2.1 Login, sessions, roles, and user accounts**
  - **Done:** 2026-09-28 · 881bdcb · reopened for D19/D20 and finished: roles, `update_user`, `@customer_required`, role-based navigation, services refusing administrators; 21 new tests; both admin refusals mutation-checked; full suite 669 passed
  - **First done:** 2026-09-28 · ab930dc · task tests 76 passed at b123c3b, then navbar/login feedback (df4f9f7), sizing rule (05b4c67), and strict email + password policy (ab930dc); full suite 536 passed; browser check by the user as admin and customer, including phone width and lockout
  - `services/users.py` (`authenticate` with lockout, `create_user` with generated temp password, `deactivate_user`, `list_users`); Flask-Login user loader (inactive users → `None`); `@admin_required`; `/signin` and `/logout` with the login page (wf-5, without the "reset password" note, D3); role-based landing redirect; login rate limit; navigation bar with the admin-only Administration link and the Profile dropdown; `flask create-admin`. Also placeholder `/account` and `/admin` pages so login lands somewhere real; tasks 4.2 and 4.5 replace them.
  - **Tests:**
    - R1.1–R1.11, R2.1–R2.7, R3.1–R3.6, R3.8, R3.10–R3.11;
    - P10 (identical failure responses), P11 (lockout timing, using the fixed clock);
    - the temp password is stored only as a hash (the page that displays it, with its `no-store` and cookie checks, is task 4.5);
    - the raw `ssn` column differs from the input (encrypted), and `list_users` returns no SSN field (D8).
  - **Depends on:** 1.1, 1.3, 1.4, 1.5 · **Requirements:** R1, R2, R3 · **Design:** Authentication and sessions, User administration

- [x] **2.2 Portfolios and bank accounts (services)**
  - **Done:** 2026-09-28 · b123c3b · task tests 48 passed (incl. P13 with 8 concurrent threads, P14 properties); full suite 478 passed
  - `services/portfolios.py` (`create_portfolio` with account-number generation and retry on collision, `list_portfolios`, `get_owned_portfolio`); `services/bank_accounts.py` (encrypt on save, mask on read). Account types come from the `ACCOUNT_TYPES` config list (Brokerage only, D14).
  - **Tests:** R4.2–R4.5, R10.1–R10.5; P13 (unique account numbers, including a forced collision that exercises the retry); P14 (routing numbers); another customer's portfolio or bank account raises `NotFound`.
  - **Depends on:** 1.1, 1.3 · **Requirements:** R4, R10 · **Design:** Portfolios, Bank accounts

- [x] **2.3 Stocks service**
  - **Done:** 2026-09-28 · ed64e82 · reopened for D16/D20 and finished: migration 003, share supply, `update_stock`, `delist_stock`; full suite 669 passed; migration applied to trading_db
  - **First done:** 2026-09-28 · b123c3b · task tests 45 passed (incl. P15 property against MySQL); full suite 478 passed
  - `services/stocks.py` (`add_stock` with validation, `DuplicateSymbol`, optional exchange, and Randomize History via 1.6 inside the same transaction; `search_stocks` with LIKE escaping; `get_stock`); `pricing.previous_close`.
  - **Tests:** R14.2–R14.6, R7.1–R7.2; P15 (search results exact, `%` and `_` literal); Randomize History writes one row per trading day for the past year, skips weekends and holidays, and ends at the entered price (R16.7); with the toggle off, only today's row exists.
  - **Depends on:** 1.1, 1.2, 1.4, 1.6 · **Requirements:** R7.1–R7.2, R14, R16.7 · **Design:** Price generator (Randomize History), Valuation and market data (search)

- [x] **2.4 Stored market schedule**
  - **Done:** 2026-09-28 · 413799d · reopened for D21 and finished: migration 004, `save_schedule` without dates; full suite 669 passed; migration applied to trading_db
  - **First done:** 2026-09-28 · b123c3b · task tests 12 passed; full suite 478 passed
  - `current_schedule` (stored row or default 09:00–17:00), `save_schedule` (close must be after open), and `market_status` using the stored schedule.
  - **Tests:** R15.3–R15.5; saving a schedule changes `is_open` results; an invalid schedule leaves the old one in place; there are no start/end dates (D21).
  - **Depends on:** 1.1, 1.4 · **Requirements:** R15 · **Design:** Market clock, Data model (DV3)

- [x] **Checkpoint 2**
  - Manual: create an admin with `flask create-admin`; in the browser, log in as the admin (lands on Administration) and as a customer created through the service (lands on Account); five wrong passwords lock the account; the navigation bar and Profile dropdown match the wireframes for each role.
  - **Previously passed:** 2026-09-28 · 18c81af · 536 passed; the user checked login, navigation, Profile, admin-only access, phone width, and lockout as admin and customer; password-change scope settled (D10) · approved by OpenTwig
  - **Reopened:** 2026-09-28 for D16, D19, D20, and D21. Tasks 2.1, 2.3, and 2.4 are done again (ed64e82, 413799d, 881bdcb; full suite 669 passed). Re-check in the browser: an administrator sees only Stocks, Administration, and History, and gets 403 on customer pages.
  - **Passed again:** 2026-09-28 · 65b23c7 · 669 passed; the user re-checked the site in the browser after the D16/D19/D20/D21 changes · approved by OpenTwig

---

## Wave 3: Money movement, trading, and prices

- [x] **3.1 Transfers: service and Transfer Funds page** (wf-7)
  - **Reopened at Checkpoint 3 (user feedback, 2026-09-28):** (1) From/To menus: account name left, balance right, no "cash" label, last 4 digits only when names repeat; (2) "too large" messages state the limit; (3) remove a bank account (D25, R10.6–R10.8, migration 007). Code and tests done: transfer and page tests 92 passed, plus money/stocks/trading/schema/migrate/app-shell tests; three mutation checks caught (removed account still usable, removal ignoring the owner, menu showing the number); migration 007 applied to trading_db. Commit e4496ea; the user checked the updated page in the browser and approved it (2026-09-28).
  - **Done:** 2026-09-28 · e4496ea · the user checked the page in the browser (transfers, menus, bank account removal) and approved it. Code and tests: deposits and withdrawals in 908b997; moves between own accounts (D15, R19, migration 005 with `.py` migration support) and the Support panel (D24) in 39a8c98; administrators get 403 since 881bdcb. Full suite 710 passed; migration 005 applied to trading_db.
  - `services/transfers.py` (`deposit`, `withdraw`) per design "Transfers"; the Transfer Funds page with From/To, amount, and date; the collapsible Add Bank Account form; the Support panel with contact details (D24); the disabled form and message when no bank account exists; `transfer.js`.
  - **Tests:** R10.3–R10.4, R11.1–R11.6, R12.1–R12.5, R19.1–R19.6; P1, P2, P4 for transfers; the transfer date must be today (A5); bank-to-bank is rejected; another customer's bank account or portfolio is rejected; balances are unchanged after every rejection (P9).
  - **Depends on:** 1.2, 2.1, 2.2 · **Requirements:** R10–R12, R19 · **Design:** Transfers, Migration 005

- [x] **3.2 Trading service**
  - **Done:** 2026-09-28 · 24baabf · task tests 55 passed (incl. Hypothesis P1–P4/P7/P8/P18, parallel buys, deadlock retry; six mutation checks all caught); full suite 766 passed
  - `services/trading.py` (`quote`, `preview_order`, `execute_order`) per design "Trading": row locks in the order Portfolio → Inventory, re-checks inside the transaction, execution at the confirm-time price, average cost, row deletion at zero, Transaction and History records; share supply (D16): the Issuer row is locked too, a buy can't exceed and reduces `shares_available`, a sell returns shares (CR08); delisted stocks can't be bought but holders can sell (D20); administrators are refused (D19).
  - **Tests:**
    - R8.3–R8.10 and R9.2–R9.9 at the service level;
    - hand-computed golden cases, including a Dollars buy that does not divide evenly, a Shares buy, a partial sell, and a full sell;
    - P3, P5, P7, P8, P9, P18 (share supply conserved);
    - "Not enough shares available" when a buy exceeds the supply (R8.11); selling a delisted stock works (R9.10);
    - concurrency: 10 parallel $100 buys against $500 of cash → exactly 5 succeed and cash ends at $0.00; parallel buys against a small share supply never oversell.
  - **Depends on:** 1.2, 2.1, 2.2, 2.3, 2.4 · **Requirements:** R8, R9, D16, D20 · **Design:** Trading, Service interfaces

- [x] **3.3 Price generator worker**
  - **Done:** 2026-09-28 · 6535eea · on automated tests only. The hands-on check (watch prices move, then stop the generator and see the "Prices may be delayed" banner) moved to Task 4.3 by the user's decision: at Checkpoint 3 there were no stocks and no page that shows prices, so there was nothing to watch. Code and tests done in 6535eea (29 task tests plus schema, migration, and page-shell tests: 199 passed; six mutation checks all caught); migration 006 applied to trading_db.
  - `pricing.tick` (in-memory full-precision prices, resync rule, `StockPrice` upsert, `SystemState` heartbeat), the run loop holding the MySQL named lock, `flask price-generator`, the stale-price context processor, and the daily value snapshot after the close with migration `006_portfolio_value_daily.sql` (D23, DV5).
  - **Tests:** R16.1–R16.7; the first tick of the day sets open = previous close (A8); nothing is written while the market is closed; a second generator exits when the lock is held; the stale flag turns on after 300 s without a tick (A17, R7.8); `exact` resyncs when the stored price changes; after the close of a trading day exactly one snapshot row per portfolio is written with the right value, and a rerun changes nothing.
  - **Depends on:** 1.6, 2.2, 2.3, 2.4 · **Requirements:** R7.8, R16, D23 · **Design:** Price generator

- [x] **3.5 First-login password change and new temporary passwords**
  - **Done:** 2026-09-28 · 9f3f840 · the user logged in with a temporary password and changed it in the browser (the old password failing afterwards is covered by the automated tests, not checked by hand). Code and tests: 29 task tests passed; full suite 565 passed; migration 002 applied to trading_db.
  - Migration `002_password_change.sql` (DV7: `must_change_password`, `temp_password_expires_at`, `session_version`; existing customers flagged); `create_user` sets the flag and a 7-day expiry (A18); `authenticate` raises `TemporaryPasswordExpired` for a correct but expired temporary password; Stay Signed In ignored while a change is required; the forced-change guard; the "Choose a new password" page at `/change-password`; `users.change_password` (N1.6 rules, not the temporary password, bumps `session_version`); `users.issue_temporary_password` (admin-only, not oneself, clears any lockout); Flask-Login IDs carry `session_version` so a change ends other logins; `flask issue-temp-password`; the login page note "New users will need to reset password upon first login." restored.
  - **Tests:** R18.1–R18.11; after the change the temporary password no longer works and the new one does; every other page redirects to `/change-password` until then (Log out still works); no remember cookie is set while a change is required; expired temporary password gets the R18.8 message only when the password is correct; a second browser logged in before the change (with and without a remember cookie) is signed out after it; a new temporary password signs the user out everywhere and resets their lockout; admins cannot issue one to themselves; migration 002 applies cleanly and flags existing customers but not administrators.
  - **Depends on:** 1.1, 2.1 · **Requirements:** R18, N1.6, D10 · **Design:** Authentication and sessions, User administration, Migration 002

- [x] **3.6 Dark mode by default with a light-mode toggle**
  - **Done:** 2026-09-28 · d1dac09 · 10 task tests passed; full suite 575 passed; the user checked the theme and button placement on desktop and phone widths
  - `data-bs-theme="dark"` on every page; `static/js/theme.js` loaded in `<head>` (applies a saved choice before the page draws, then wires the button); a sun/moon theme button on every page: just left of Profile on wider screens and next to the menu button on phones when logged in, at the right of the navbar on the login page; Bootstrap Icons `sun` and `moon-stars` inlined in `templates/_theme_icons.html` with their MIT licence recorded; `rem` sizing in `app.css`.
  - **Tests:** R2.8–R2.9: pages render dark by default; `theme.js` is served and loaded in `<head>`; the theme button with both icons and an accessible label is on the login page and on logged-in pages; no template uses fixed-colour classes (`bg-white`, `bg-light`, `bg-dark`, `text-dark`, `text-white`, `text-black`); `theme.js` uses no `eval`/`innerHTML`. Clicking the button and the saved choice surviving a reload are checked in a browser.
  - **Depends on:** 1.5 · **Requirements:** R2.8, R2.9, D13 · **Design:** Pages and routes (Colour theme), DD11
- [x] **Checkpoint 3**
  - Manual: with a few stocks added (through the stocks service), run `flask price-generator` while the market is open (or with a schedule that makes it open) and watch prices change in the database; a second generator exits; deposit and withdraw through the Transfer Funds page and confirm the balances; log in as the test customer with a temporary password, get sent to "Choose a new password", change it, and confirm the old password no longer works; pages open in dark mode, the sun button switches to light, and the choice survives a reload.
  - **Automated:** 2026-09-28 · 6535eea · full suite 795 passed. Browser checks by the user, 2026-09-28: Transfer Funds page (3.1) and first-login password change (3.5) passed. The generator check moved to 4.3 (see 3.3); a second generator exiting and the old password failing are covered by automated tests.

---

## Wave 4: Pages

- [x] **4.1 Trade page** (wf-2)
  - **Done:** 2026-09-28 · 28dab09 · the user checked the page in the browser (quote, ticket, review, placing an order, with the market open) and approved it after the feedback below. Was remaining: the user's browser check (buy and sell during market hours, both themes, phone width). Code and tests done: `app/trade` (page, quote API, preview, confirm), `trade.js`, a `shares` display filter, and a Trade button on the stock detail page; task tests 48 passed plus app-shell, app, stocks-page, and transfer-page tests (113 passed); four mutation checks caught (a reusable review, no expiry, uncapped Search History, reading the amount from the confirm form). Browser feedback (2026-09-28): added a Market value row and greyed out Sell with no shares; 51 task tests passed, one more mutation check caught.
  - `/trade`, `/api/quote/<symbol>`, `/trade/preview`, `/trade/confirm`; the ticket revealing its fields after a quote; the confirmation screen; the order confirmation; Search History (A13); the market status badge; the Legal Information accordion; `trade.js` (quote lookup, disabling double submit).
  - **Tests:** route-level R8.1–R8.9 and R9.1–R9.8; an expired or reused confirmation shows the `OrderExpired` message; **tampered form fields** (price, total, quantity, another customer's portfolio ID) are ignored or rejected and the server recomputes everything; the quote API returns 404 for another customer's portfolio.
  - **Depends on:** 3.2 · **Requirements:** R8, R9 · **Design:** Trading, Pages and routes

- [x] **4.2 Account summary, portfolio creation, and positions** (wf-3, wf-4)
  - **Done:** 2026-09-28 · 7946fdc · the user checked Summary and Positions in the browser and approved them after two feedback fixes (whole Portfolios row clickable, fac783a; % columns coloured like the $ columns, 7946fdc). Was remaining: the user's browser check (Summary, Create Portfolio, Positions, Reinvest?, both themes, phone width). Code and tests done: `valuation.account_summary`/`positions`, `portfolios.set_reinvest`, the Summary and Positions pages, `account.js`; percentages per D26 (asked 2026-09-28); task tests 29 passed (golden case worked by hand) plus portfolios, stocks-page, app-shell, app, and trade-page tests (151 passed); five mutation checks caught (pending transfers counted, moves ignored, no per-position rounding, wrong day-change base, Reinvest? without the owner check).
  - `services/valuation.py` (`account_summary`, `positions`); the Summary page with the Portfolios table and Investments Total row; Create Portfolio (A11); the Positions page with the Equities and Cash groups; empty states; value-over-time charts and YTD change from the daily snapshots (D23); the Reinvest? toggle per position (D22); no Mutual Funds group (D17).
  - **Tests:** golden valuation cases computed by hand (market value, price change, day change, cost basis, gain/loss, account value, totals across two portfolios); R4.1, R4.5, R5.1–R5.6, R6.1–R6.7; YTD excludes deposits (hand-computed case); the Reinvest? toggle saves and changes nothing else.
  - **Depends on:** 3.1, 3.2, 3.3 · **Requirements:** R4, R5, R6, D22, D23 · **Design:** Valuation and market data

- [x] **4.3 Stocks pages** (wf-1)
  - **Done:** 2026-09-28 · edcc6c2 · the user checked the pages in the browser, including the check carried over from 3.3: with dev hours set to 00:00–23:55, prices moved while `flask price-generator` ran and "Prices may be delayed" appeared after it stopped. Was remaining: the user's browser check (both themes, phone width) and the generator check below. Code and tests done: `services/valuation.py` (`top_gainers`, `trending`, `search`, `stock_detail`), `/stocks` and `/stocks/<symbol>`, `stocks.js`; task tests 28 passed plus app-shell, app, price-generator, and stocks-service tests (132 passed); three mutation checks caught (UTC day boundary for Trending, ranking by the rounded %, delisted stocks shown).
  - Search page with Top Gainers (A14); the stock detail view with a Chart.js price chart and sparklines; Trending (most traded today, D18, A19); Most Active, Top Losers, and Bid/Ask omitted (D5, D17); delisted stocks hidden (D20); the stale-price banner.
  - **Browser check carried over from 3.3:** with the seed stocks (4.6) and the market open (or hours set so it is), run `flask price-generator` and watch prices change on the Stocks page; stop it and confirm the "Prices may be delayed" banner appears after 5 minutes (R16, R7.8, A17).
  - **Tests:** R7.1–R7.9; Top Gainers and Trending order (hand-built trades today and yesterday); the chart data matches the `StockPrice` rows; pages contain no inline scripts (CSP).
  - **Depends on:** 2.3, 3.3 · **Requirements:** R7 · **Design:** Valuation and market data, Pages and routes

- [x] **4.4 History page** (wf-8)
  - **Done:** 2026-09-29 · 802f82d · the user checked the page and its printout in the browser and approved them after the print fix (all columns fit; the PDF is named after the account and dates). The admin view is covered by tests, not checked by hand. Was remaining: the user's browser check (filters, print preview, admin view, both themes, phone width). Code and tests done: `services/history.py`, `/history`, `history.js`, print styles (the nav bar gets `d-print-none`); task tests 28 passed (incl. the Hypothesis P16 test and the 23:30 Arizona boundary) plus app-shell, app, theme, and auth-route tests (91 passed); four mutation checks caught (UTC day bounds, no owner check, transfers under a symbol filter, oldest first).
  - `services/history.py`; filters with the A12 date ranges; description text for trades and transfers (including "Transfer to/from <account>" for moves between accounts, R19.4); print layout; the admin view listing every customer's portfolios.
  - **Tests:** R13.1–R13.7; P16; Arizona date boundaries (a trade at 23:30 Arizona, which is 06:30 UTC the next day, is listed under its Arizona date); transfer rows have a blank Price; a customer cannot select another customer's portfolio.
  - **Depends on:** 3.1, 3.2 · **Requirements:** R13 · **Design:** History

- [x] **4.5 Administration page** (wf-6)
  - **Done:** 2026-09-29 · da710bc · the user tested every function in the browser and approved it after the feedback changes: Users and Stocks on their own rows (7a6cefd), both lists paged 10 a page (1c4ea9d, c687472), and thousands separators in number boxes (9c86d82, D27). Was remaining: the user's browser check (every form and list, both themes, phone width). Code and tests done: the `/admin` page and its eight POST routes, `valuation.admin_stocks`, `admin.js`; task tests 59 passed plus app-shell, app, auth-route, password-change, users/stocks/market service, stocks-page, and theme tests (278 passed); four mutation checks caught (no `no-store`, password in a flash, SSN box refilled, Randomize History ignored); the flash and SSN ones first slipped through, so a test was tightened and a redundant route line removed.
  - Add User with the temp password shown once; a Role choice with access level and department for administrators (D19); the Users list with edit (D20), deactivate, "Issue new temporary password" (R18.9), and no SSN column (D8); Add Stocks with Number of Shares (D16) and the Stocks list with edit and delist (D20); the Market Time form with open and close times only (D21).
  - **Tests:** route-level R3.1–R3.12, R14.1–R14.9, R15.3–R15.5, R18.9–R18.10; customers get 403 on every admin route; an admin cannot deactivate themselves or issue themselves a temporary password; every temporary password (new user or reissued) is shown once, never appears in the session cookie or flash messages, and its response has `Cache-Control: no-store` (R3.4–R3.5).
  - **Depends on:** 2.1, 2.3, 2.4, 3.5 · **Requirements:** R3, R14, R15, R18 · **Design:** User administration, Pages and routes

- [x] **4.6 Development seed data** (`flask seed`)
  - **Done:** 2026-09-28 · task tests 6 passed (two mutation checks caught: no development-only guard, no skip of existing stocks); run on trading_db: 10 sample stocks and the demo customer added. Needs an administrator first (design, Sample data).
  - Moved from Wave 3: its opening deposit goes through the transfers service (3.1).
  - Sample stocks via `stocks.add_stock(..., randomize_history=True)` with share supplies and a demo customer with a random password printed once, with a Brokerage portfolio, a bank account, and an opening deposit made through the transfers service.
  - **Tests:** refuses to run unless `APP_ENV=development`; running it twice leaves the same row counts; no fixed passwords exist in code.
  - **Depends on:** 2.1, 2.2, 2.3, 3.1 · **Requirements:** none (developer tool) · **Design:** Local development (Sample data)

- [x] **Checkpoint 4**
  - Manual: every page is reachable from the navigation bar as an admin and as a customer (no 404s, no template errors), and each matches its wireframe apart from items parked on open questions.
  - **Automated:** 2026-09-29 · da710bc · full suite 1071 passed (8 min 42 s).
  - **Manual:** the user tested every page and function in the browser as an administrator and as a customer (2026-09-29), after the Wave 4 feedback changes recorded on tasks 4.1-4.5.

---

## Wave 5: Verification

Waves 1–4 each test their own piece. Wave 5 tests the finished application as a whole and attacks it the way a real user or attacker would. Every task here:

- adds **automated tests that stay in the suite permanently**, so the checks re-run on every future change;
- records what was run, the results, and any problems found in `docs/test-report.md` (date, commit, commands, pass/fail counts, issues);
- fixes any bug it finds in the same task, or adds a new unchecked task for it in the right wave.

- [x] **5.1 SQL injection testing**
  - **Done:** 2026-09-29 · `tests/security`: 200 passed (182 injection tests, 18 static-check tests); a planted f-string injection in the stock search failed 10 of them; no problems found. Three calls that can't use parameters carry a reviewed `# sql-safe:` reason. sqlmap (optional) not run. Details in `docs/test-report.md`.
  - A payload list in `tests/security/payloads.py`: `' OR '1'='1' -- `, `'; DROP TABLE \`User\`; --`, `" OR ""="`, `1 UNION SELECT email, password_hash FROM \`User\` --`, `admin'#`, `' OR SLEEP(5) -- `, lone `%` and `_`, backslashes, a NUL byte, Unicode look-alike quotes, and a 10,000-character string.
  - Send **every payload through every text input on every route**: login email and password; stock search; trade symbol and the quote API path; the history symbol filter; admin Add User (name, email) and Add Stocks (name, symbol, description, exchange); bank account fields; portfolio name.
  - For each, assert: no 500 error; no login bypass; no response slower than 2 seconds (catches `SLEEP` injection); table row counts unchanged apart from the intended insert; accepted values are stored exactly as typed; search treats `%` and `_` literally.
  - A **static check test** that scans `app/` for `.execute(` calls whose SQL is built with an f-string, `%` formatting, `.format()`, or `+` concatenation, and fails with the file and line.
  - Optional, local only (never against a shared or production server): run `sqlmap` against the local dev server's login and search endpoints and record the result in the test report.
  - **Depends on:** Wave 4 · **Requirements:** N2.1, N2.2 · **Properties:** P15

- [x] **5.2 Server-side math verification**
  - **Done:** 2026-09-29 · `tests/verification`: 30 passed (the hand-computed scenario, 10 tampering, 17 rounding, the new P1–P9 state machine); the state machine with `HYPOTHESIS_PROFILE=thorough` (1,000 sequences) passed in 13 min 31 s; two planted bugs (average cost truncated, supply not returned on a sell) are caught. No application problems found. Details in `docs/test-report.md`.
  - An **end-to-end scenario** with every expected value computed by hand and written as a table in the test (and in the test report): deposit $1,000.00 → buy $250.00 of a $33.33 stock (Dollars) → buy 2.5 shares of a $120.10 stock (Shares) → set new prices → sell half of the first position → withdraw $100.00. After every step, assert the cash balance; each position's quantity, average cost, market value, and gain/loss; the Summary and Positions totals; and the History rows' amounts, quantities, and prices.
  - **Tampering:** submit preview, confirm, and transfer forms with altered or extra fields (price, total, quantity, cash balance, another customer's IDs). The results must be identical to the untampered request, or it must be rejected.
  - **Rounding edges:** amounts that do not divide evenly; $0.01 purchases; the maximum amount (99,999,999.99); quantities with 6 decimal places; average cost after mixed buys and sells (P8).
  - Run the P1–P9 state machine once with `HYPOTHESIS_PROFILE=thorough` (1,000 examples) and record the result.
  - **Depends on:** Wave 4 · **Requirements:** R8, R9, R11, R12, R17.3, the Calculations table · **Properties:** P1–P9

- [x] **5.3 Access control and session testing**
  - **Done:** 2026-09-29 · `tests/security/test_access_control.py` 13 passed (route × role matrix, ownership, forced password change, and P12, all generated from the URL map; a dedicated SSN last-4 test); the P17 check now inspects every response in the suite, full suite 1314 passed; three planted bugs caught (a missing role guard, a missing owner check, a CSRF exemption). Session behaviours already tested in Waves 2–3 are listed in `docs/test-report.md`. No application problems found.
  - A route × role matrix **generated from the app's URL map**, so a new route cannot be missed: anonymous → redirect to `/signin`; customer → 403 on admin routes; customer A → 404 on every route and parameter that takes customer B's IDs (portfolios, bank accounts, the quote API's portfolio, history).
  - P12 over every form found by rendering each page (CSRF token present; POST without it → 400).
  - Sessions: logout clears both cookies; a deactivated user's existing session and remember cookie stop working; a password change or reissued temporary password signs out every other browser; while a password change is required, every route except Log out redirects to `/change-password` (R18); lockout (P11); the login rate limit returns 429.
  - Data exposure (P17): test fixtures use distinctive SSNs and account numbers, and a check applied to **every response in the whole test suite** fails if an SSN, any part of it, or a full account number appears. The temp password never appears in cookies or flash messages.
  - **Depends on:** Wave 4 · **Requirements:** R1, R2, R3.5–R3.6, N1, N2.3 · **Properties:** P10–P12, P17

- [x] **5.4 Input validation and XSS testing**
  - **Done:** 2026-09-29 · 213 passed (210 boundary cases over every form field; stored XSS in every displayed text field, checked on every page as both roles); a planted `| safe` was caught. No application problems found. Details in `docs/test-report.md`.
  - Boundary tests for **every form field**: empty, whitespace only, minimum and maximum length, one character over the maximum, wrong formats (SSN, routing number, dates such as 02/30/2026, a future date of birth); for number fields: 0, negative, more than 2 decimals, non-numeric, scientific notation, NaN/Infinity, and values above the column maximum. Every rejection shows a field error and changes nothing.
  - XSS: store `<script>alert(1)</script>` and `"><img src=x onerror=alert(1)>` in every text field that is shown later (user names, nicknames, stock descriptions, portfolio names) and assert they are rendered escaped on every page that displays them; the CSP header blocks inline scripts.
  - **Depends on:** Wave 4 · **Requirements:** R3.1–R3.3, R4.2–R4.3, R10.1–R10.2, R11.3, R12.2, R14.2–R14.3, N2.2

- [x] **5.5 Concurrency and integrity testing**
  - **Done:** 2026-09-29 · `tests/verification/test_concurrency.py` 13 passed, 4 runs out of 4: withdrawal, buy/sell, last-shares, mixed transfer, generator-tick, and lock-wait races, each followed by P1–P4 and P18 checks; `trading_app` is refused UPDATE/DELETE/TRUNCATE on the audit tables; removing the portfolio row lock makes the races fail. No application problems found.
  - Parallel requests: buys against limited cash; withdrawals against limited cash; a buy and a sell on the same position; deposits during trading; price ticks during trades (the generator waits while a trade holds the Issuer row lock, D16, and results stay consistent); the deadlock and lock-timeout retry path.
  - After each run: P1–P4 hold, no cash balance is negative, and no zero-quantity position exists.
  - Audit immutability: connected as `trading_app`, `UPDATE` and `DELETE` on `History` and `Transaction` fail, and no application code path edits them.
  - **Depends on:** Wave 4 · **Requirements:** N2.5, N6, R8.10, R9.9, R17

- [x] **5.6 Manual end-to-end walkthrough**
  - **Done:** 2026-09-29 · the user checked every page against its wireframe as a customer and as an administrator (A1–A10 pass). Of the error messages and other checks, four were seen in the browser during Wave 4 (market closed, stale-price banner, printing, moving prices); the others were not triggered by hand this time, and each is covered by a named automated test in `docs/test-report.md`. No problems found.
  - Against the local dev server (venv, seeded data, generator running): walk every page as an admin and as a customer and compare each with its wireframe in `docs/reference/images/`; trigger every message in design "Error handling"; print the History page; stop the generator and confirm the stale-price banner appears; set the schedule so the market is closed and confirm trades are refused.
  - Record each step as pass/fail in `docs/test-report.md`, and add a task for every bug found.
  - **Depends on:** Wave 4 · **Requirements:** all pages · **Design:** Pages and routes, Error handling

- [x] **Checkpoint 5**
  - Manual: `docs/test-report.md` covers tasks 5.1–5.6 with results; every problem found is either fixed (with a test) or listed as an open task; the `thorough` Hypothesis run passed.
  - **Done:** 2026-09-29 · 9414a60 · full suite 1540 passed (15 min 1 s); `docs/test-report.md` covers 5.1–5.6 with results; no application problems were found, so none are open; the `thorough` Hypothesis run of the P1–P9 state machine passed (1,000 sequences, 5.2).

---

## Wave 6: Production readiness

- [x] **6.1 Production configuration and hardening**
  - **Done:** 2026-09-29 · with `APP_ENV=production` the app refuses a weak or missing `SECRET_KEY`, `FLASK_DEBUG`, the MySQL `root` account, insecure cookie flags, and running as root; `deploy/gunicorn.conf.py` (loopback bind for the Cloudflare Tunnel, no version in `Server`, refuses root), systemd units for the web app and the price generator (non-root, hardened), a cloudflared example, and log redaction (`app/logs.py`). Hosting recorded as D28 (self-hosted Proxmox LXC first, AWS later). Task tests 38 passed plus config, app-shell, auth-route, and app tests (152 passed); full suite 1578 passed; five mutation checks caught (debug guard, weak key, root process, all-interfaces bind, an SSN written to the log).
  - With `APP_ENV=production`, the app refuses to start if debug is on, cookie flags are not secure, or `SECRET_KEY` is missing or weak; Gunicorn configuration (workers, non-root user); service definitions for the web app and the price generator; logging that never writes secrets or decrypted values; responses do not reveal server software or versions (no `Server: Werkzeug/… Python/…` or `gunicorn/…` header).
  - **Tests:** each startup guard; production responses carry no version-revealing `Server` header.
  - **Depends on:** Wave 5 · **Requirements:** N1, N3.1, N3.4, N3.6 · **Design:** Security design, Deployment

- [ ] **6.2 Deployment runbook and backups**
  - **In progress:** Claude, 2026-09-29 · `docs/deployment.md` written for the self-hosted stage (container, MySQL, settings, services, tunnel, updates, 3-2-1 backups, restore test, troubleshooting). `/health` now checks the database (200, or 503 with no details; `test_app` 2 new tests, 149 passed with the shared app, auth, password-change, and production tests; a planted "ok without asking" is caught). Remaining: a restore actually tested on the container (the user, runbook step 14). **Open, postponed by the user (2026-09-29):** the tunnel runs on a separate VM, so Gunicorn listens on the container's LAN address (10.1.0.14:8000, layout B), and the Proxmox firewall rule limiting port 8000 to the tunnel VM (runbook step 9B) is not set up yet. Until it is, LAN devices can reach the site over plain HTTP and fake the forwarded-address header; traffic through Cloudflare is unaffected.
  - `docs/deployment.md` turning design "Production" into concrete steps; 3-2-1 backups plus a **tested restore**; extend `/health` (added in 0.1) with a database check for uptime monitoring. Written for the first, self-hosted stage (Proxmox LXC + Cloudflare Tunnel, D28), with notes for the later cloud stage.
  - **Depends on:** Wave 5 · **Requirements:** N3 · **Design:** Deployment

- [ ] **6.3 Market days and manual open** (wf-6 Market Time; asked for by the user, 2026-10-18)
  - **In progress:** Claude, 2026-10-18 · code, specs (D29, R15.3/R15.5/R15.7-R15.9, DV12), and tests done; `test_admin_page` 87 passed; calendar, schedule, schema, generator, trade-page, trading, stocks, app-shell, theme, migrate, input-validation, and access-control tests 672 passed, 12 failed. The 12 failures (`test_schema`: app-account privilege checks and the db-upgrade message) fail the same way on the unchanged commit 60c549d: the local `.env` sets `DB_USER=trading_admin`, so the "app account is denied" checks run as an administrator. Migration 008 applied to the local `trading_db`. **Remaining:** the user's browser check of the Market Time card (both themes, phone width, dropdown and switch).
  - Migration 008 (`MarketSchedule.trading_days`, `force_open`; DV12); `Schedule` gains trading days and the force-open flag, used by `is_open`, `next_open`, `market_status`, and the generator's snapshot check; the Market Time card gets a days dropdown with checkboxes, a Force market open switch, one Submit at the bottom of the card, and an open/closed status with a green or red dot; the Trade page badge reads "Market open" while forced open.
  - **Tests:** R15.3, R15.5 (no day chosen), R15.7 (forced open beats hours, unchosen days, and holidays; switching it off returns to the schedule), R15.8 (one POST saves all three), R15.9 (green/red dot and text, forced-open text); days other than Monday to Friday open and close the market; `next_open` skips unchosen days; no snapshot while forced open; tampered day values are refused; migration 008 keeps existing schedules on Monday to Friday.
  - **Depends on:** 2.4, 3.3, 4.1, 4.5 · **Requirements:** R15, D29 · **Design:** Market clock, Data model (DV12), Pages and routes

- [ ] **Checkpoint 6**
  - Manual: starting with `APP_ENV=production` and an unsafe setting (debug on, weak key) is refused; a backup is restored into a scratch database and the data checks out.

---

## Future improvements (after the task list)

Ideas raised during the build that are **not** part of the current specs. They wait until Checkpoint 6 is done; then the user picks which to build. Each one gets a decision in `docs/requirements.md` and becomes a normal task before any code is written. Add new ideas here instead of building them mid-wave.

- **F1. Search suggestions (autocomplete)** (raised 2026-09-28, during 4.3). While typing in the Stocks search bar (and the Trade page's Symbol field), show up to 8 matching listed stocks in a dropdown: symbol and name on the left, price on the right, like the Transfer From/To menus. Arrow keys and Enter pick one; Escape closes it. Needs a small JSON route reusing `stocks.search_stocks` (login required, listed stocks only, query escaped), shared JavaScript for both fields, and screen-reader support (ARIA combobox). Estimate: 1–2 hours with tests. The simpler `<datalist>` alternative was considered and not preferred: its look varies by browser and doesn't follow dark mode.
- **F2. Selling positions worth less than a cent** (raised 2026-09-28). Every order total must be at least $0.01 (`trading.size_order`), so a leftover position worth under half a cent (e.g. 0.0005 shares at $5.66 = $0.0028) can't be sold and stays in the account. Proposed fix: allow a sell of the **whole** position whose total rounds to $0.00; the shares are removed and the sale is recorded at $0.00 (never rounded up to $0.01, which would create money). Needs a money-rule decision (R9, A3), changes to `size_order`/`execute_order`, and tests (whole position only; partial sells still need $0.01; History shows the $0.00 sale). Estimate: under an hour.

---

## Parked: waiting on open questions

Nothing is parked: every open question has been answered (see Decisions in `docs/requirements.md`). If a new question comes up, park its work here.

---

## Requirements coverage

| Requirement | Tasks |
|---|---|
| R1 Login and sessions | 1.5, 2.1, 5.3 |
| R2 Navigation and access | 2.1, 3.6, 5.3 |
| R3 User administration | 1.3, 2.1, 4.5, 5.3, 5.4 |
| R4 Create portfolio | 2.2, 4.2 |
| R5 Account summary | 3.3, 4.2, 5.2 |
| R6 Positions | 3.3, 4.2, 5.2 |
| R7 Stock search and quotes | 2.3, 3.3, 4.3 |
| R8 Buy stock | 3.2, 4.1, 5.2, 5.5 |
| R9 Sell stock | 2.3, 3.2, 4.1, 5.2, 5.5 |
| R10 Bank accounts | 2.2, 3.1, 5.4 |
| R11 Deposit | 3.1, 5.2 |
| R12 Withdraw | 3.1, 5.2 |
| R13 History | 4.4 |
| R14 Stock management | 2.3, 4.5 |
| R15 Market schedule | 1.4, 2.4, 4.5, 6.3 |
| R16 Price generator | 1.6, 2.3, 3.3 |
| R17 Audit trail | 1.1, 3.1, 5.2, 5.5 |
| R18 First-login password change | 3.5, 4.5, 5.3 |
| R19 Transfers between own accounts | 3.1, 4.4 |
| N1 Confidentiality | 1.3, 1.5, 2.1, 3.5, 5.3, 6.1 |
| N2 Integrity | 1.1, 1.5, 5.1, 5.4, 5.5 |
| N3 Availability and operations | 0.2, 1.1, 6.1, 6.2 |
| N4 Data precision | 1.1, 1.2 |
| N5 Technology | 0.1 |
| N6 Performance | 5.5 |

---

## Notes and lessons learned

Things discovered while working that the next person or AI session would otherwise have to rediscover: environment quirks, gotchas, and contracts between pieces of code (for example, the exact variables a template expects from its route). Add entries as you go, newest last, in the form `- <date> · <topic>: <note>`. Correct or remove an entry if it stops being true.

- 2026-09-28 · Git on Windows PowerShell 5.1: double quotes inside a `git commit -m` message are mangled when passed to git. Write the message to a file and use `git commit -F <file>`. Write that file without a byte-order mark (`[IO.File]::WriteAllText(path, text, (New-Object System.Text.UTF8Encoding($false)))`): `Out-File -Encoding utf8` adds one, and git keeps it at the start of the summary line.
- 2026-09-28 · MySQL grants: table-level `GRANT` needs the table to exist, so `flask db-upgrade` applies `trading_app`'s grants after migrating (and the test harness does the same). This needs `trading_migrate` to hold `GRANT OPTION` on the two databases; without it, db-upgrade fails with 1142 "GRANT command denied". Re-running `scripts/setup_local_db.py` fixes that.
- 2026-09-28 · MySQL on Windows stores table names in lower case (`lower_case_table_names=1`): `information_schema` lists `user`, `transaction`, and so on. SQL still works with the ERD's capitalisation, but code or tests that compare table names must compare case-insensitively.
- 2026-09-28 · Windows: Python's `zoneinfo` needs the `tzdata` package for `America/Phoenix`; Gunicorn does not run on Windows, so local development uses `flask run`.
- 2026-09-28 · Local Python: the project `.venv` was created with Python 3.14.6 (the minimum is 3.11). All pinned packages installed on 3.14 without problems.
- 2026-09-28 · Local MySQL: 8.0.46, running as the `MySQL80` Windows service, which is set to **manual** start. After a reboot, start it (Services, or `Start-Service MySQL80` from an admin shell) before running the app or the tests.
- 2026-09-28 · MySQL accounts and hosts: this workstation **is** `10.0.0.62`, the "LAN database host" of an earlier design. The old `appuser@10.0.0.%` account only accepts logins arriving from a 10.0.0.x address, so connecting through `localhost` (`127.0.0.1`) is refused with 1045 "Access denied" even with the right password, and `GRANT ... TO 'appuser'@'localhost'` fails with 1410 because that account does not exist. In MySQL the host is part of the account name. This project uses `@'localhost'` accounts; the developer admin account is `trading_admin@localhost` (not root).
- 2026-09-28 · Tests: never hold an app context open (e.g. the `app_ctx` fixture) while making requests with the test client. Flask reuses that context for every request, so `flask.g` leaks between requests: Flask-WTF's cached CSRF token skipped saving a new token into the post-login session, and the next POST failed with "The CSRF session token is missing." Wrap direct service calls in a short-lived `with web_app.app_context():` instead (see the `svc` fixture in `tests/test_password_change.py`).
- 2026-09-28 · python-dotenv: `load_dotenv()` with no path searches from the *calling file's* directory, not the working directory. `create_app()` uses `find_dotenv(usecwd=True)` so a `.env` is only picked up from where the command runs, which keeps tests run from a temporary directory isolated from a developer's real `.env`.

---

## Dependency graph (machine-readable)

For tools and AI agents that schedule work. **The Depends on lines above are the source of truth.** Whenever a task, wave, or dependency changes, update this graph in the same commit, then check it with `.venv\Scripts\python scripts\check_task_graph.py docs\tasks.md` (it fails if the graph and the Depends on lines disagree, or if a task depends on another task in its own wave). Each wave starts only after the previous wave's checkpoint (`CP<n>`); each checkpoint requires every task in its wave. Parked items are not included.

```json
{
  "source_of_truth": "Depends on lines in docs/tasks.md",
  "waves": [
    { "wave": 0, "after": null,  "tasks": ["0.1", "0.2"],                               "checkpoint": "CP0" },
    { "wave": 1, "after": "CP0", "tasks": ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6"],   "checkpoint": "CP1" },
    { "wave": 2, "after": "CP1", "tasks": ["2.1", "2.2", "2.3", "2.4"],                 "checkpoint": "CP2" },
    { "wave": 3, "after": "CP2", "tasks": ["3.1", "3.2", "3.3", "3.5", "3.6"],          "checkpoint": "CP3" },
    { "wave": 4, "after": "CP3", "tasks": ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6"],   "checkpoint": "CP4" },
    { "wave": 5, "after": "CP4", "tasks": ["5.1", "5.2", "5.3", "5.4", "5.5", "5.6"],   "checkpoint": "CP5" },
    { "wave": 6, "after": "CP5", "tasks": ["6.1", "6.2", "6.3"],                        "checkpoint": "CP6" }
  ],
  "depends_on": {
    "0.1": [],
    "0.2": [],
    "1.1": ["0.1", "0.2"],
    "1.2": ["0.1"],
    "1.3": ["0.1"],
    "1.4": ["0.1"],
    "1.5": ["0.1"],
    "1.6": ["0.1"],
    "2.1": ["1.1", "1.3", "1.4", "1.5"],
    "2.2": ["1.1", "1.3"],
    "2.3": ["1.1", "1.2", "1.4", "1.6"],
    "2.4": ["1.1", "1.4"],
    "3.1": ["1.2", "2.1", "2.2"],
    "3.2": ["1.2", "2.1", "2.2", "2.3", "2.4"],
    "3.3": ["1.6", "2.2", "2.3", "2.4"],
    "3.5": ["1.1", "2.1"],
    "3.6": ["1.5"],
    "4.1": ["3.2"],
    "4.2": ["3.1", "3.2", "3.3"],
    "4.3": ["2.3", "3.3"],
    "4.4": ["3.1", "3.2"],
    "4.5": ["2.1", "2.3", "2.4", "3.5"],
    "4.6": ["2.1", "2.2", "2.3", "3.1"],
    "5.1": ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6"],
    "5.2": ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6"],
    "5.3": ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6"],
    "5.4": ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6"],
    "5.5": ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6"],
    "5.6": ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6"],
    "6.1": ["5.1", "5.2", "5.3", "5.4", "5.5", "5.6"],
    "6.2": ["5.1", "5.2", "5.3", "5.4", "5.5", "5.6"],
    "6.3": ["2.4", "3.3", "4.1", "4.5"]
  },
  "needs_approval": {}
}
```
