# CLAUDE.md

Stock trading simulator (Team 1 capstone). A web app where customers fund portfolios and buy/sell stocks at simulated prices, and administrators manage users, stocks, and market hours. The source design is `docs/reference/Systems Analysis Design - Team 1.docx`; its diagrams are exported to `docs/reference/images/` (`pm-*` process maps, `wf-*` wireframes, `erd.png`, `network-topology.png`). Open those images instead of unzipping the .docx.

## Specs

Work is driven by three spec files (Kiro-style) in `docs/`. Read the relevant one before starting a feature:

- `docs/requirements.md`: user stories and acceptance criteria (R1, R2, …), traced to the SAD's CR/AR/SR IDs; also scope, decisions, and open questions
- `docs/design.md`: data model, process flows, pages, and technical design
- `docs/tasks.md`: tasks grouped into waves, with status checkboxes and the definition of done

## Testing and "done" (non-negotiable)

- Never claim code works, or check off a task, without running its tests **in the venv** and seeing them pass. Follow the Definition of done in `docs/tasks.md`: the task's tests (plus the tests for any shared code it changed) pass, UI pages are checked in a browser, and the result is recorded on the task's **Done** line.
- Run the **full suite only at each wave's checkpoint**, not after every task (it takes about 5 minutes; the user's decision).
- Every task ships with tests. Never delete, skip, or weaken a test to make it pass; fix the code.
- When reporting results, quote the actual pytest summary line (e.g. "212 passed"). If something was not run or could not be verified, say so plainly.
- Before starting a task, mark it **In progress** in `docs/tasks.md`; if it is left unfinished, record what remains.

Cite requirement IDs in commit messages, test names, and task entries (e.g. `test_buy_rejected_when_market_closed  # R8.4`). If the specs and the code disagree, or a spec is silent, ask; do not guess. Do not build anything listed as an open question in `docs/requirements.md` until it is resolved.

**Source of truth:** the wireframes and the ERD win over the SAD's prose and process maps. When they conflict, build what the wireframe/ERD shows and note the difference in the specs.

## Stack

- Python + Flask, Jinja templates, Bootstrap for styling, plain JavaScript for interactivity
- MySQL for all persistent data
- Gunicorn in production; the Flask dev server is for local development only
- Do not introduce a frontend framework (React, Vue, etc.), a different CSS framework, or a different database without asking.

## Development environment

- **Always use the project virtual environment** `.venv` (repo root, git-ignored) for installing, running, testing, and debugging. Never install packages into or run the app with the global Python.
  - Create: `python -m venv .venv`, then `.venv\Scripts\python -m pip install -r requirements-dev.txt`
  - Run tools through the venv's interpreter (e.g. `.venv\Scripts\python -m pytest`, `.venv\Scripts\python -m flask …`) so commands work even when the venv is not activated in the current shell.
- Local MySQL runs on `localhost:3306` (database `trading_db`; tests use `trading_test`). Details: "Local development" in `docs/design.md`.
- Never run the Flask dev server with `--host 0.0.0.0` while debug is on (the Werkzeug debugger allows code execution).

## Commands

Run from the repo root. All use the venv's interpreter.

- Tests: `.venv\Scripts\python -m pytest` (one file: `.venv\Scripts\python -m pytest tests\test_config.py -v`)
- Dev server: `.venv\Scripts\python -m flask --app app run --debug` → `http://127.0.0.1:5000` (`/health` returns `{"status": "ok"}`)
- Local database setup: `.venv\Scripts\python scripts\setup_local_db.py`. The **user** runs this, because it asks for their MySQL admin username and password. They do not use root; the project admin account is `trading_admin` (`--admin-user trading_admin`). To verify with the `.env` credentials (no admin login needed): add `--check`.
- Task graph check (after changing task dependencies): `.venv\Scripts\python scripts\check_task_graph.py docs\tasks.md`
- Migrations, admin creation, price generator, seed: added in later tasks (see `docs/tasks.md`).
- Local MySQL is the `MySQL80` Windows service (set to manual start). If connections fail, check it is running: `Get-Service MySQL80`.

## Git

- Commit locally to keep a version history: small commits at logical checkpoints.
- Every commit message has both parts, even for one-line changes:
  - **Summary**: one line, imperative mood, under ~70 characters (e.g. "Add buy-order validation").
  - **Description**: after a blank line, explain what changed and why, listing the main changes as bullets when there are several. Cite requirement IDs where relevant (e.g. R8.4).
- Commit directly on `main`; do not create feature branches unless asked. (A separate production branch may be added later.)
- **Never rewrite pushed history.** The user pushes to the Gitea remote (`origin`) themselves, sometimes mid-session. Before any amend, rebase, or reset of existing commits, run `git fetch` and `git status -sb`; if the commit is already on `origin`, make a new commit instead.
- **Never push without explicit permission.** That covers `git push` of any kind (including tags and new branches) and anything else that publishes to the remote, such as opening a pull request. Ask every time; permission for one push does not carry over to the next.

## Security rules (apply to every change)

- **SQL**: parameterized queries only. Never build SQL with f-strings, `%` formatting, or concatenation of user input.
- **Forms**: every POST form carries a CSRF token; every input is validated server-side, even if the page also validates it in JavaScript.
- **Passwords**: store only a salted hash (e.g. `werkzeug.security`); never log or return them.
- **Sensitive data**: SSNs and bank account numbers are encrypted at rest (`cryptography` library). SSNs are never displayed, not even partially; account numbers are displayed masked, last 4 digits only.
- **Secrets**: keys, DB credentials, and encryption keys come from environment variables. Never commit them.
- **Auth**: failed logins show the generic "Invalid email or password"; never reveal whether the email exists.
- **Authorization**: admin-only routes and actions are enforced on the server, not just hidden in the nav. Customers can only read or modify their own portfolios, bank accounts, and history.
- **Least privilege**: the app connects with a DB user that has only the permissions it needs; the server never runs as root.
- **Errors**: show users friendly messages; never expose stack traces or SQL errors.

## Domain rules

- **Money**: use `Decimal`, never `float`. Currency columns are `DECIMAL(10,2)`; share quantities are `DECIMAL(18,6)` (fractional shares allowed).
- **Atomicity**: every change to a cash balance or holding is written in one DB transaction together with its Transaction/Transfer record and History entry. A failure rolls back everything.
- **Validation before writes**: business checks (market open, sufficient funds, sufficient shares) run in the application layer before anything is written, and are re-checked inside the DB transaction.
- **Timestamps**: every created record gets a `DATETIME(3)` timestamp.
- **Prices**: there is no live market feed. All prices come from the random price generator.

## Conventions

- Table and column names follow the ERD (snake_case columns, e.g. `cash_balance`, `ticker_symbol`).
- User-facing messages use the exact wording given in the acceptance criteria.
- The page is "Positions" (the wireframes misspell it "Posititions").
- **Dark mode by default (D13):** pages start dark, and the navbar's sun/moon button (`static/js/theme.js`) switches to light. Use Bootstrap's colour-mode-aware classes (`bg-body`, `bg-body-tertiary`, `text-body`, `text-body-secondary`, `border`), never fixed colours (`bg-white`, `bg-light`, `bg-dark`, `text-dark`, `text-white`, `text-black`), and check new pages in both themes. A test enforces the class rule.
- **Responsive, relative sizing (D9):** use Bootstrap's grid, utilities, and breakpoints for layout; in `app.css` use `rem`, `%`, viewport units, or Bootstrap CSS variables, never `px`; no inline `style=` and no `width=`/`height=` attributes in templates. Tests in `tests/test_app_shell.py` enforce this. The site will get a mobile-friendly pass later, so keep every page easy to adapt.
