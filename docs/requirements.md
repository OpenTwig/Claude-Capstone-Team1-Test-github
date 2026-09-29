# Requirements Document

**Project:** Stock Trading System (Team 1 capstone)
**Source:** `docs/reference/Systems Analysis Design - Team 1.docx` (the "SAD"): Tyler Layes, Jacob Nelson, Thorin Ochs. Diagrams: `docs/reference/images/`.
**Companion specs:** `docs/design.md` (how), `docs/tasks.md` (build order)

## Introduction

Customers currently have no self-service way to fund an investment account and buy or sell stock. Every deposit, withdrawal, and order depends on a staff member, there is no single system of record for cash, holdings, and trades, service stops outside business hours, and administrators have no controlled way to manage the list of tradable stocks or the market's hours.

This system is a web application that gives customers self-service portfolios, cash transfers, and stock trading, and gives administrators control over users, stocks, and market hours. All cash balances, holdings, and trades live in one MySQL database. There is no live market feed: a random price generator simulates a market for training purposes.

### How to read this document

- Each requirement has a user story and numbered acceptance criteria written in EARS form (`WHEN … THEN the system SHALL …`, `IF … THEN …`, `WHILE …`). Cite criteria as `R8.4` (requirement 8, criterion 4).
- **Sources** lines point back to the SAD: requirement IDs (CR = customer, AR = administrator, SR = system), process maps (PM), and wireframes (WF).
- Where the SAD is silent, a criterion may reference an **assumption** (`A#`). Assumptions are defaults to build unless the team changes them.
- Items marked with an **open question** (`Q#`) are unresolved. Do not build them until the question is answered.
- **Source of truth (D1):** where the SAD's prose or process maps conflict with the wireframes or ERD, the wireframes and ERD win.

## Glossary

| Term | Meaning |
|---|---|
| Customer | A user who holds portfolios, transfers cash, and trades stock. |
| Administrator (Admin) | A user with an `Admin` record who manages users, stocks, and market hours. |
| Portfolio | A customer's brokerage account holding cash and stock positions. Has an account name, account type, unique account number, and cash balance. A customer may have several. |
| Bank Account | A customer's external bank account, used as the source of deposits and the destination of withdrawals. |
| Stock (Issuer) | A tradable company created by an admin: company name, ticker symbol, current price, description. |
| Position (Inventory) | The quantity of one stock held in one portfolio, with its average cost. |
| Trade (Transaction) | A filled buy or sell of a stock in a portfolio. |
| Transfer | A cash movement between a bank account and a portfolio (deposit or withdrawal). |
| History | The audit-trail record written for every trade and transfer. |
| Market open | The period when trading is allowed and prices move: inside the market schedule, on a trading day that is not a holiday. |
| Previous close | A stock's `close_price` on the most recent earlier trading day. |
| Price generator | The background process that simulates price movement while the market is open. |

### Calculations

These definitions apply wherever the values are displayed.

| Value | Definition |
|---|---|
| Market value | quantity × current price |
| Cost basis | quantity × average cost |
| Gain/loss | market value − cost basis |
| Price change ($) | current price − previous close |
| Price change (%) | price change ($) ÷ previous close × 100 |
| Day change (position) | quantity × price change ($) |
| Account value | portfolio cash balance + sum of its positions' market values |
| Total value | sum of account values across the customer's portfolios |
| Average cost after a buy | (old quantity × old average cost + bought quantity × price) ÷ new quantity |

Money is rounded to the cent (half-up); share quantities to 6 decimal places (rounded down when derived from a dollar amount).

## Scope

### In scope

Login and sessions; admin-managed user accounts; portfolios; bank accounts; deposits and withdrawals; buying and selling stock at simulated prices; account summary, positions, stock search, and transaction history pages; admin management of stocks and market hours; the random price generator; the audit trail; and the security controls in the SAD's Security and Privacy Architecture.

### Out of scope

- Customer self-registration (D2)
- Self-service password reset, a "change password any time" option, and any email delivery (D3, D10). Administrators issuing a new temporary password *is* in scope (R18).
- **Most Active** and **Top Losers** panels on the Stocks page (D5). Daily gains and losses are already computable from `StockPrice` open/close prices, so these can be added later.
- Live market data feeds
- Real money movement: bank transfers are recorded, not sent to a real bank
- Order types other than Market, and timings other than Day Only (A4)
- Mutual funds and bid/ask prices (D17)
- Setting stock prices by hand, and relisting a delisted stock (D20)
- Dividends
- A dedicated mobile-friendly design pass (planned for later; D9 keeps pages ready for it)

## Decisions

Decisions made by the team that resolve gaps or conflicts in the SAD.

| ID | Decision |
|---|---|
| D1 | The wireframes and ERD are the source of truth. Where the SAD's prose or process maps disagree, follow the wireframes and ERD. |
| D2 | Only administrators create user accounts. On creation the system generates a temporary password and shows it to the admin to give to the user. No email service is used. (Overrides the Technology Solution's "users register".) |
| D3 | Self-service password resets are not needed for now. *(Revised by D10: the login wireframe's note "New users will need to reset password upon first login" is now implemented.)* |
| D4 | Market holidays are hard-coded or loaded from a library (e.g. the NYSE calendar in the Python `holidays` package). Admins do not manage holidays. |
| D5 | Most Active and Top Losers are out of scope. |
| D6 | All market times are in Arizona time (America/Phoenix, UTC−7, no daylight saving). |
| D7 | Deleting a user deactivates the account instead of removing it, so their records and history are kept. |
| D8 | SSNs are never displayed, not even the last 4 digits, once entered. (Overrides the Administration wireframe's "last 4 ID num" column.) |
| D10 | New users must replace their temporary password on first login; there is no other way for users to change their password. Temporary passwords expire after 7 days (A18). Administrators can issue a new temporary password to any other user, which puts that user back into the first-login flow and is how forgotten passwords are handled without email. Changing a password ends the user's other logins. (R18) |
| D11 | Trade confirmation (was Q1): Submit shows a confirmation screen of the order; the trade executes at the stock's price at the moment the customer confirms, so the confirmation labels its price and total as estimates. A Dollars order never spends more than the amount entered. |
| D12 | Single price generator (was Q3): the generator is its own process, and a MySQL named lock ensures only one copy moves prices, even with several web servers. |
| D13 | Dark mode is the default look. A sun icon in the navigation bar switches to light mode (a moon icon switches back), and each browser remembers the choice. Built on Bootstrap's colour modes. |
| D14 | Portfolio account types (was Q6): the only type is **Brokerage**, the ordinary account people use to buy and sell stocks. More types can be added later without schema changes. |
| D15 | Transfers between a customer's own brokerage accounts are allowed (was Q10). Each account keeps its own cash; a move takes cash from one and adds it to the other, with no bank account involved (R19). |
| D16 | Share supply (was Q2): each stock has a limited number of shares. Add Stock collects it (AR03); a buy can't exceed the shares available and reduces them; a sell returns shares (CR08). |
| D17 | Mutual funds and bid/ask (was Q4): not built. Positions shows equities and cash; quotes show price, change, open, and previous close. |
| D18 | Trending Stocks (was Q5): the 5 stocks with the most customer trades today (A19). |
| D19 | Administrator accounts (was Q7): created from Add User by choosing Administrator (with access level and department). Administrators manage the system and do not trade: they hold no portfolios, their menu is Stocks, Administration, and History, and customer-only pages refuse them. |
| D20 | Editing and removing (was Q8): administrators can edit a user's name, email, and date of birth (not SSN or role) and a stock's name, description, and exchange (not symbol or price; prices come only from the generator). Deleting a stock *delists* it: it disappears from search and can't be bought, but customers who hold it can still sell. |
| D21 | Market Time form (was Q9): open and close times only, no dates; the hours apply every trading day until changed. |
| D22 | Reinvest? column (was Q11): shown on Positions as a Yes/No toggle the customer can change; it has no effect, since there are no dividends. |
| D23 | Value charts and YTD (was Q12): the price generator saves each portfolio's value once per trading day after the close; charts plot those snapshots, and YTD change = value change since the start of the year minus net deposits this year. |
| D24 | Support panel (was Q13): contact information (a support email address and hours, from configuration). |
| D25 | Customers can remove a linked bank account from the Transfer Funds page (asked for by the team at Checkpoint 3). The account is hidden, not deleted, so past transfers still show where money came from or went (R17). |
| D9 | Pages are built to adapt to any screen size: relative units (rem, %, viewport units, Bootstrap variables) instead of pixels, and Bootstrap's responsive grid and collapsing navbar. A full mobile-friendly pass comes later; this keeps it easy. |
| D26 | Percentages on Summary and Positions (R5.1, R6.2, asked at task 4.2; the specs said only "% of total"): brokerage style. Day change % = day change ÷ value at the previous close (value − day change); YTD change % = YTD change ÷ (value at the start of the year + net money put in this year); Gain/loss % = gain/loss ÷ cost basis. A position's Day Change % equals its Price Change %. When the base is $0.00 the percentage shows 0.00%. |
| D27 | Thousands separators (asked for by the team, 2026-09-29): every money and share-quantity box accepts commas grouping the whole part in threes ("1,000", "25,000.50", "1,000,000.5"). Commas anywhere else ("1,00", "10,00", ",100") are refused rather than guessed, so a decimal comma is never misread. Applies to transfers, the Trade ticket, and Add Stocks (price and number of shares). |
| D28 | Hosting in two stages (the team's decision, 2026-09-29, at task 6.1): first **self-hosted** on the team's own servers, in an Ubuntu-based Proxmox LXC container published through a Cloudflare Tunnel; later the **cloud** (AWS) layout in the SAD's network topology. Self-hosted, Cloudflare terminates HTTPS and filters traffic. `cloudflared` forwards to Gunicorn either from inside the container (on 127.0.0.1) or from a separate tunnel VM, with the container's port firewalled to that VM alone; the team's setup uses a tunnel VM and the container at 10.1.0.14. MySQL runs in the same container. Both stages run with `APP_ENV=production` and meet N1–N3's security goals; the 99.9% availability target (N3.3) belongs to the cloud stage, since one container has no failover. |

---

## Functional requirements

### Requirement 1: Login and sessions

**User Story:** As a customer or administrator, I want to sign in with my email and password, so that I can reach my own pages securely.

**Sources:** PM Login; WF5 Log in Page; Security and Privacy Architecture

#### Acceptance Criteria

1. WHEN an unauthenticated user requests any page other than the login page THEN the system SHALL redirect them to the login page.
2. The login page SHALL collect Email, Password, and a Stay Signed In checkbox, with a Submit button.
3. WHEN the user submits a matching email and password THEN the system SHALL create an authenticated session.
4. IF the email does not exist or the password does not match THEN the system SHALL display "Invalid email or password" and let the user try again, without revealing which field was wrong.
5. WHEN Stay Signed In is checked THEN the system SHALL keep the session across browser restarts for a limited duration (A2); otherwise the session SHALL end when the browser closes.
6. WHEN an administrator signs in THEN the system SHALL display the Administration page.
7. WHEN a customer signs in THEN the system SHALL display the Account (Summary) page.
8. IF an account has too many consecutive failed login attempts THEN the system SHALL lock it temporarily and reject further attempts until the lock expires (A1).
9. The system SHALL rate-limit requests to the login endpoint.
10. WHEN the user clicks Log out THEN the system SHALL end the session and return to the login page.
11. The system SHALL set session cookies as HttpOnly, Secure (in production), and SameSite.

### Requirement 2: Navigation and role-based access

**User Story:** As a user, I want a consistent navigation bar that only shows what I'm allowed to use, so that I can move between pages without hitting areas I can't access.

**Sources:** WF1–WF8 (shared nav bar); AR01; Security and Privacy Architecture (authorization)

#### Acceptance Criteria

1. Every authenticated page SHALL show a navigation bar with the user's pages, Profile, and Log out, and SHALL highlight the current page. A customer's pages are Account, Positions, Trade, Transfer Funds, Stocks, and History.
2. WHERE the user is an administrator, the navigation bar SHALL show Stocks, Administration, and History (D19).
3. WHERE the user is a customer, the navigation bar SHALL NOT show Administration.
4. IF a non-administrator requests an administrator page or action THEN the system SHALL refuse it on the server (HTTP 403), regardless of what the navigation shows.
5. IF a customer requests a portfolio, bank account, transfer, trade, or history record that belongs to another customer THEN the system SHALL refuse it.
6. WHEN the user clicks the Profile icon THEN the system SHALL show a dropdown with the user's full name and email address.
7. WHERE the user is an administrator, the Profile dropdown SHALL also show their access level and department.
8. Every page, including the login page, SHALL be shown in dark mode by default (D13).
9. The navigation bar SHALL show a theme button on every page: a sun icon while in dark mode, which switches to light mode, and a moon icon while in light mode, which switches back. The choice SHALL be remembered in that browser (D13).
10. IF an administrator requests a customer-only page (Account, Positions, Trade, Transfer Funds) or action THEN the system SHALL refuse it on the server (HTTP 403), because administrators do not trade (D19).

### Requirement 3: User account management (administrator)

**User Story:** As an administrator, I want to create and manage user accounts, so that customers can be onboarded without self-registration or email.

**Sources:** WF6 Administration Page (Add User, Users list); SR09; D2; D3; ERD `User`, `Admin`

#### Acceptance Criteria

1. The Add User form SHALL collect Full Name (1–255 characters), SSN (format XXX-XX-XXXX), Email (a well-formed address: at most 254 characters and 64 before the @, the limits for a deliverable address; the ERD column allows 320), Date of Birth (MM/DD/YYYY, in the past), and Role (Customer or Administrator). For an administrator it SHALL also collect Access Level (1–20 characters) and an optional Department (up to 100 characters) (D19).
2. IF any field is missing or invalid, including a malformed SSN or an impossible date, THEN the system SHALL display a validation error and create nothing.
3. IF the email already belongs to a user THEN the system SHALL reject the form with a validation error.
4. WHEN the administrator submits a valid form THEN the system SHALL create the user, record the creation timestamp (SR09), generate a random temporary password, and display that password to the administrator once. The user must replace it at first login (R18).
5. The system SHALL store only a salted hash of the temporary password and SHALL NOT display it again after the confirmation.
6. The system SHALL store the SSN encrypted, and SHALL NOT display any part of it anywhere in the UI once it has been entered (D8).
7. The Users list SHALL show each user's Name, Email, Date of Birth, and Role, with edit and delete actions. It SHALL NOT show any part of the SSN (D8).
8. The system SHALL NOT send email as part of account creation.
9. An administrator account SHALL be created through the same form by choosing Administrator. Administrators hold no portfolios and do not trade (D19); the first administrator comes from `flask create-admin`.
10. WHEN an administrator deletes a user THEN the system SHALL deactivate the account rather than remove it, keeping the user's portfolios, transactions, transfers, and history (D7).
11. IF a deactivated user tries to log in THEN the system SHALL reject the login with "Invalid email or password".
12. WHEN an administrator edits a user THEN the system SHALL let them change the Full Name, Email, and Date of Birth, validated as in R3.1–R3.3; the SSN and role SHALL NOT be editable (D20).

### Requirement 4: Create portfolio

**User Story:** As a customer, I want to open one or more portfolios, so that I can hold cash and stock in separate accounts.

**Sources:** PM Create Portfolio; WF3 Summary Page (Portfolios panel); ERD `Portfolio`

#### Acceptance Criteria

1. The customer SHALL be able to start Create Portfolio from the Account (Summary) page (A11).
2. The Create Portfolio form SHALL collect Account Name (1–100 characters) and Account Type. The only type is Brokerage (D14).
3. IF Account Name or Account Type is missing THEN the system SHALL display a validation error and create nothing.
4. WHEN the form is valid THEN the system SHALL generate a unique account number (A10), save the portfolio linked to the customer with a cash balance of $0.00 and a creation timestamp, and display a confirmation showing the new account number.
5. The new portfolio SHALL appear in every portfolio selector for that customer.

### Requirement 5: Account summary (customer landing page)

**User Story:** As a customer, I want a summary of all my portfolios and cash, so that I can see what I own at a glance.

**Sources:** WF3 Summary Page; CR09

#### Acceptance Criteria

1. The Summary page SHALL show Total Value, Day Change ($ and %), and Year-to-date Change ($ and %) across all the customer's portfolios.
2. The Summary page SHALL show a chart of total value over time, built from the daily value snapshots (D23).
3. The Portfolios table SHALL list each portfolio with Account Name, account number (partly hidden, last 4 shown), Account Type, Cash, Account Value, Day Change, and YTD Change.
4. The Portfolios table SHALL end with an Investments Total row summing the Cash, Account Value, Day Change, and YTD Change columns.
5. The Portfolios table SHALL provide a search box that filters the listed portfolios.
6. WHEN the customer has no portfolios THEN the system SHALL show an empty state that directs them to create one.

### Requirement 6: Positions

**User Story:** As a customer, I want to see the detail of every position in a portfolio, so that I can track value, cost, and gain or loss per stock.

**Sources:** WF4 Positions Page; CR09; ERD `Inventory`

#### Acceptance Criteria

1. The Positions page SHALL provide a portfolio selector showing Account Name and the last 4 of the account number.
2. For the selected portfolio, the Portfolio Value panel SHALL show Total Value, Day Change ($ and % of total), Year-to-date Change ($ and % of total), Total Cost Basis, and Total Gain/Loss ($ and % of total), plus a value-over-time chart from the daily snapshots (D23).
3. The Position Details table SHALL list each equity position with Symbol, Quantity, Price, Price Change $, Price Change %, Market Value, Day Change $, Day Change %, Cost Basis, and Gain/Loss $.
4. The Equities group SHALL be collapsible and SHALL end with a Total row.
5. The page SHALL show a Cash group whose total equals the portfolio's cash balance, with change and gain/loss shown as $0.00 / 0% / N/A.
6. The page SHALL NOT show a Mutual Funds group (D17).
7. Each equity position SHALL have a Reinvest? Yes/No toggle the customer can change. It is saved per position and has no other effect (D22).

### Requirement 7: Stock search and quotes

**User Story:** As a customer, I want to search the stocks admins have listed and see their prices and recent performance, so that I can decide what to trade.

**Sources:** WF1 Stock Search Page(s); SR04; D5

#### Acceptance Criteria

1. The Stocks page SHALL provide a search box that matches stocks by case-insensitive partial match on ticker symbol or company name.
2. IF no stocks match the search THEN the system SHALL display "No stocks found matching "<query>"".
3. Before a search, the page SHALL show a Top Gainers panel (A14) and a Trending Stocks panel (the 5 stocks with the most customer trades today, A19, D18), each listing symbol, company name, a small price chart, current price, and day change.
4. WHEN the customer selects a stock THEN the system SHALL show its company name and symbol, current price, $ daily change, % daily change, a price-over-time chart built from its daily price history, Previous Close, Open, Stock Description, and Exchange if one was entered.
5. The system SHALL display each stock's current market price wherever the stock is shown (SR04).
6. The Most Active and Top Losers panels SHALL NOT be shown (D5).
7. Bid and Ask SHALL NOT be shown (D17).
9. Delisted stocks SHALL NOT appear in search results or in the Top Gainers and Trending panels (D20).
8. WHILE the market is open, IF prices have not been updated within the stale-price threshold (A17) THEN every page showing prices SHALL show a "Prices may be delayed" indicator.

### Requirement 8: Buy stock

**User Story:** As a customer, I want to buy stock with a portfolio's cash while the market is open, so that I can invest.

**Sources:** PM Buy Stock; WF2 Stock Trading Page(s); CR01, CR03, CR04, CR05, CR06, SR05, SR06, SR07, SR08, SR10

#### Acceptance Criteria

1. The Trade page SHALL show a portfolio selector (Account Name, last 4 of account number) and the selected portfolio's Available Cash.
2. WHEN the customer enters a symbol THEN the system SHALL show the stock name, current price, market status (open/closed), and change since previous close, and SHALL list the customer's recently entered symbols as Search History (A13). IF the symbol is not listed, or its stock has been delisted, THEN the system SHALL display "Symbol not found" (D20).
3. The trade ticket SHALL collect Action (Buy), Unit (A3), Amount, Order Type (A4), and Timing (A4), with Clear and Submit buttons. WHEN submitted THEN the system SHALL calculate the quantity and total amount.
4. IF the market is closed THEN the system SHALL reject the order with "Market is closed" (CR01, SR05, SR06).
5. IF the portfolio's cash balance is less than the total amount THEN the system SHALL reject the order with "Insufficient funds" (CR03).
6. WHEN the checks in 8.4 and 8.5 pass THEN the system SHALL display a confirmation of the order (symbol, action, quantity, price, total) before executing it (CR04). The confirmation labels the price and total as estimates; the trade executes at the price at the moment of confirming (D11).
7. WHEN the customer confirms THEN the system SHALL re-run the checks in 8.4 and 8.5 and, in a single database transaction:
   - decrease the portfolio's cash balance by the total amount (CR06);
   - decrease the stock's shares available by the quantity (D16);
   - add the quantity to the portfolio's position in that stock, creating it if needed, and update its average cost (SR07);
   - record a Transaction with type `Buy`, status `Filled`, order type, timing, price per share, total amount, and a timestamp (SR10);
   - record a History event for the trade (SR08).
8. WHEN the trade completes THEN the system SHALL display an order confirmation.
9. The system SHALL NOT allow a trade to be cancelled after the customer confirms it (CR05).
10. IF any step of 8.7 fails THEN the system SHALL roll back all of it and leave balances and positions unchanged.
11. IF the quantity exceeds the stock's shares available THEN the system SHALL reject the order with "Not enough shares available" (D16).

### Requirement 9: Sell stock

**User Story:** As a customer, I want to sell stock I hold while the market is open, so that I can take proceeds back into cash.

**Sources:** PM Sell Stock; WF2 Stock Trading Page(s); CR02, CR04, CR05, CR07, CR08, SR05, SR06, SR07, SR08, SR10

#### Acceptance Criteria

1. WHEN the customer selects a portfolio and a symbol held in it THEN the system SHALL show the quote (as in 8.2) and the number of shares owned.
2. The trade ticket SHALL collect Action (Sell), Unit (A3), Amount, Order Type (A4), and Timing (A4). WHEN submitted THEN the system SHALL calculate the quantity and total proceeds.
3. IF the market is closed THEN the system SHALL reject the order with "Market is closed" (CR02, SR05, SR06).
4. IF the quantity exceeds the shares owned THEN the system SHALL reject the order with "Quantity exceeds shares owned".
5. WHEN the checks in 9.3 and 9.4 pass THEN the system SHALL display a confirmation before executing (CR04, D11).
6. WHEN the customer confirms THEN the system SHALL re-run the checks in 9.3 and 9.4 and, in a single database transaction:
   - increase the portfolio's cash balance by the total proceeds (CR07);
   - reduce the position's quantity, leaving its average cost unchanged, and delete the position when it reaches zero (SR07);
   - return the sold shares to the stock's shares available (CR08, D16);
   - record a Transaction with type `Sell`, status `Filled`, and a timestamp (SR10);
   - record a History event for the trade (SR08).
7. WHEN the trade completes THEN the system SHALL display an order confirmation.
8. The system SHALL NOT allow a trade to be cancelled after the customer confirms it (CR05).
9. IF any step of 9.6 fails THEN the system SHALL roll back all of it.
10. A customer SHALL be able to sell a stock they hold even after it has been delisted (D20).

### Requirement 10: Bank accounts

**User Story:** As a customer, I want to link my bank accounts, so that I can move cash in and out of my portfolios.

**Sources:** PM Add Bank Account; WF7 Fund Transfer Page (Add Bank Account); ERD `BankAccount`

#### Acceptance Criteria

1. The Add Bank Account form on the Transfer Funds page SHALL collect Bank Name (1–100 characters), Account Number, Routing Number, and Nickname (1–100 characters), with Clear and Submit buttons, and SHALL be collapsible.
2. IF any field is missing or the Routing Number is not exactly 9 digits THEN the system SHALL display a validation error and save nothing.
3. WHEN the form is valid THEN the system SHALL save the bank account linked to the customer with a creation timestamp, and display a confirmation.
4. WHEN a bank account is saved THEN it SHALL appear in the From and To selectors on the Transfer Funds page.
5. The system SHALL store the bank account number encrypted and SHALL display only its last 4 digits.
6. The Add Bank Account panel SHALL list the customer's linked bank accounts (nickname, bank name, last 4 digits), each with a Remove button (D25).
7. WHEN the customer removes a bank account THEN the system SHALL display a confirmation, the account SHALL no longer appear in the From and To selectors, and it SHALL NOT be usable for new transfers. Past transfers and their History SHALL still show it (R17.2).
8. A customer SHALL only be able to remove their own bank accounts (R2.5).

### Requirement 11: Deposit cash

**User Story:** As a customer, I want to deposit money from my bank account into a portfolio, so that I have cash to invest.

**Sources:** PM Deposit Cash; WF7 Fund Transfer Page; CR11, SR11; ERD `Transfer`

#### Acceptance Criteria

1. The Transfer Funds form SHALL collect From, To, Cash Amount, and Transfer Date (mm/dd/yyyy), with Clear and Submit buttons.
2. A deposit SHALL be a transfer whose From is one of the customer's bank accounts and whose To is one of the customer's portfolios.
3. IF the amount is not greater than $0.00 or the Transfer Date is invalid (A5) THEN the system SHALL display a validation error and change nothing.
4. WHEN the form is valid THEN the system SHALL, in a single database transaction: create a Transfer with type `Deposit` and status `Pending`; increase the portfolio's cash balance by the amount; set the Transfer status to `Completed`; and record a History event with a timestamp (SR11).
5. WHEN the deposit completes THEN the system SHALL display a confirmation with the portfolio's new cash balance.
6. IF the customer has no linked bank account THEN the page SHALL display "Link a bank account before transferring funds", and deposits and withdrawals SHALL NOT be possible. The form SHALL stay usable only if the customer has two or more brokerage accounts to move cash between (R19).

### Requirement 12: Withdraw cash

**User Story:** As a customer, I want to withdraw available cash from a portfolio to my bank account, so that I can use my money elsewhere.

**Sources:** PM Withdraw Cash; WF7 Fund Transfer Page; CR12, SR11; ERD `Transfer`

#### Acceptance Criteria

1. A withdrawal SHALL be a transfer whose From is one of the customer's portfolios and whose To is one of the customer's bank accounts.
2. IF the amount is not greater than $0.00 or the Transfer Date is invalid (A5) THEN the system SHALL display a validation error and change nothing.
3. IF the amount exceeds the portfolio's cash balance THEN the system SHALL display "Insufficient funds" and change nothing.
4. WHEN the checks pass THEN the system SHALL, in a single database transaction: create a Transfer with type `Withdrawal` and status `Pending`; decrease the portfolio's cash balance by the amount; record the funds as sent to the bank account; set the Transfer status to `Completed`; and record a History event with a timestamp (SR11).
5. WHEN the withdrawal completes THEN the system SHALL display a confirmation with the portfolio's new cash balance.
6. Moving cash directly between two of the customer's own brokerage accounts is covered by R19 (D15).
7. The Support panel SHALL show a short contact message with the support email address and hours from configuration (D24, A20).

### Requirement 13: Transaction history

**User Story:** As a customer, I want to search and print my past trades and transfers, so that I have a record of my activity. As an administrator, I want to review any customer's history, so that I can audit the system.

**Sources:** PM View Transaction History; WF8 History Page; CR10, SR08; Security and Privacy Architecture (audit trail)

#### Acceptance Criteria

1. The History page SHALL provide a portfolio selector (Account Name, last 4 of account number), a Date Range selector defaulting to Last 6 Months (A12), an optional Symbol filter, and a Search button.
2. WHEN the customer searches THEN the system SHALL list the portfolio's trades and transfers in the date range (and matching the symbol, if given), showing Date, Transaction Type, Symbol, Description, Quantity, Price, Amount, and Status, most recent first.
3. WHEN results are found THEN the system SHALL display "Transactions found from <start date> to <end date>".
4. IF no records match THEN the system SHALL display "No transactions found".
5. WHEN the user clicks the print icon THEN the system SHALL print the transaction list, using a print layout that omits the navigation bar and search controls.
6. WHERE the user is an administrator, the portfolio selector SHALL include every customer's portfolios.
7. A customer SHALL only see their own portfolios' history.

### Requirement 14: Stock management (administrator)

**User Story:** As an administrator, I want to add and maintain the stocks available for trading, so that the tradable list is controlled and documented.

**Sources:** PM Add Stock; WF6 Administration Page (Add Stocks, Stocks list); AR01, AR02, AR03, SR01; ERD `Issuer`, `StockPrice`

#### Acceptance Criteria

1. Only administrators SHALL be able to create, modify, or delete stocks (AR01).
2. The Add Stocks form SHALL collect Stock Name (the company name, 1–255 characters), Stock Symbol (1–10 characters, uppercase letters and digits only), Stock Price, Stock Description (A15), and a Randomize History toggle (AR02, AR03), plus an optional Exchange (up to 10 characters; in the ERD but not the wireframe), and Number of Shares available (greater than 0, up to 6 decimal places; AR03, D16).
3. IF any required field is missing or invalid, or the price is not greater than $0.00, THEN the system SHALL display a validation error naming the field and create nothing.
4. IF the symbol already exists THEN the system SHALL display "Symbol already exists" and create nothing.
5. WHEN the form is valid THEN the system SHALL save the stock with the entered price as its current price (SR01), make it tradable, and display a confirmation.
6. WHERE Randomize History is on, the system SHALL generate random daily open and close prices for past trading days (A9) ending at the entered price.
7. The Stocks list SHALL show each stock's Name, Symbol, Price, Change %, and Shares available, with edit and delist actions (D20).
8. WHEN an administrator edits a stock THEN the system SHALL let them change the Stock Name, Description, and Exchange; the symbol and price SHALL NOT be editable (D20).
9. WHEN an administrator delists a stock THEN it SHALL no longer be available to buy or to find in search, and customers holding it SHALL still be able to sell it (D20).

### Requirement 15: Market schedule

**User Story:** As an administrator, I want to control when the market is open, so that trading hours are consistent and documented.

**Sources:** WF6 Administration Page (Market Time); AR04, AR05; D4

#### Acceptance Criteria

1. By default the market SHALL be open Monday through Friday, 9:00 AM to 5:00 PM Arizona time (D6), and closed on holidays (AR05).
2. The system SHALL determine holidays from a hard-coded list or a holiday library such as the NYSE calendar (D4). Administrators SHALL NOT manage holidays.
3. The Market Time form SHALL let an administrator change the market's hours (AR04) using an open time and a close time; the new hours apply every trading day until changed (D21).
4. WHEN an administrator saves a valid schedule THEN the system SHALL apply it to all market-open checks and the price generator.
5. IF the end is not after the start THEN the system SHALL display a validation error and keep the current schedule.
6. The Trade page SHALL show whether the market is currently open or closed.

### Requirement 16: Random stock price generator

**User Story:** As the system, I want to move stock prices randomly while the market is open, so that customers trade in a realistic simulated market.

**Sources:** PM Random Stock Price Generator; SR01, SR02, SR03, SR04; ERD `Issuer.current_price`, `StockPrice`

#### Acceptance Criteria

1. WHILE the market is open, the generator SHALL run automatically at random intervals (A7).
2. On each run, the generator SHALL, for every stock: apply a random percentage change to the current price, and update today's close price in the stock's price history to match (SR02).
3. Each change SHALL be small enough that prices move gradually (A7), and a price SHALL never fall below $0.01.
4. On the first run of each trading day, the generator SHALL create that day's price history row with the open price equal to the previous close (A8).
5. WHILE the market is closed, the generator SHALL take no action and prices SHALL stay unchanged (SR03).
6. Exactly one generator SHALL run at a time, even when several web servers are deployed (D12).
7. The price history SHALL hold exactly one row per stock per trading day, with the stock, date, open price, and close price.

### Requirement 17: Audit trail

**User Story:** As an administrator, I want every money and stock movement recorded permanently, so that disputes can be resolved and the database stays the authoritative record.

**Sources:** Problem Statement; Security and Privacy Architecture (integrity controls); SR08, SR09, SR10, SR11; ERD `History`

#### Acceptance Criteria

1. Every completed trade, deposit, and withdrawal SHALL produce exactly one History record referencing the portfolio and the Transaction or Transfer, with an action and a millisecond-precision timestamp. A transfer between two of the customer's accounts SHALL produce one History record for each account (R19.4).
2. The system SHALL NOT provide any way to edit or delete History, Transaction, or Transfer records through the application.
3. For every portfolio, the cash balance SHALL equal its completed deposits, transfers in from its other accounts, and sell proceeds, minus its completed withdrawals, transfers out to its other accounts, and buy totals.

### Requirement 18: First-login password change and new temporary passwords

**User Story:** As a new customer, I want to replace the temporary password my administrator gave me with one only I know, so that my account is private. As an administrator, I want to issue a new temporary password to a user who has forgotten theirs, so that they can get back in without email.

**Sources:** WF5 Log in Page ("New users will need to reset password upon first login"); D3 as revised by D10; A18; N1.6

#### Acceptance Criteria

1. WHEN an administrator creates a user (R3.4) or issues a new temporary password THEN the system SHALL mark the account as needing a password change, and the temporary password SHALL expire 7 days after it was issued (A18).
2. The login page SHALL show the note "New users will need to reset password upon first login."
3. WHEN a user logs in with an unexpired temporary password THEN the system SHALL display the "Choose a new password" page, and every other page except Log out SHALL redirect to it until the password has been changed.
4. WHILE a password change is required, the system SHALL ignore Stay Signed In, so a temporary password never gets a long-lived login.
5. The "Choose a new password" page SHALL collect New password and Confirm new password.
6. IF the two entries differ, the new password breaks the password rules (N1.6), or it equals the temporary password THEN the system SHALL display a validation error and keep the change required.
7. WHEN a valid new password is submitted THEN the system SHALL store only its hash, clear the change requirement and the expiry, end the user's other logins, and take the user to their landing page (R1.6, R1.7). The temporary password SHALL no longer work.
8. IF a user enters the correct temporary password after it has expired THEN the system SHALL refuse the login with "Your temporary password has expired. Ask an administrator for a new one." A wrong password still gets "Invalid email or password" (R1.4).
9. An administrator SHALL be able to issue a new temporary password to any other user from the Users list. The system SHALL generate it, display it to the administrator once with the same protections as R3.4–R3.5, replace the user's current password, and end the user's existing logins.
10. An administrator SHALL NOT be able to issue a temporary password to themselves through the Users list. A developer with server access can use `flask issue-temp-password` (for example, when the only administrator is locked out).
11. The lockout rules (R1.8) SHALL apply to temporary passwords as to any other password.

### Requirement 19: Transfers between a customer's own brokerage accounts

**User Story:** As a customer with more than one brokerage account, I want to move cash from one to another, so that I can fund trades in the account I choose without going through my bank.

**Sources:** D15 (was Q10); ERD `Transfer.from_portfolioID` / `to_portfolioID`; WF7 Fund Transfer Page

#### Acceptance Criteria

1. The Transfer Funds page SHALL let a customer choose one of their brokerage accounts as From and a different one of their own as To. No bank account is needed.
2. The same amount and date rules as deposits and withdrawals SHALL apply (R11.3, A5). IF the amount exceeds the From account's cash THEN the system SHALL display "Insufficient funds" and change nothing. IF From and To are the same account THEN the system SHALL display a validation error.
3. WHEN valid THEN the system SHALL, in a single database transaction: create a Transfer with type `Internal` (status `Pending`, then `Completed`) naming both accounts; decrease the From account's cash and increase the To account's cash by the amount; and record History.
4. The transfer SHALL appear in both accounts' History: a "Transfer out" record on the From account and a "Transfer in" record on the To account, each timestamped (R17.1).
5. WHEN the transfer completes THEN the system SHALL display a confirmation with both accounts' new cash balances.
6. A customer SHALL only move cash between their own accounts. Any other customer's account SHALL be refused (R2.5).

---

## Non-functional requirements

### N1: Confidentiality

**Sources:** Security and Privacy Architecture (confidentiality)

1. The application SHALL be served over HTTPS in production.
2. Passwords SHALL be stored only as salted hashes.
3. SSNs and bank account numbers SHALL be encrypted at rest. SSNs SHALL never be displayed (D8); bank account numbers SHALL be displayed masked to the last 4 digits.
4. Secrets (session key, database credentials, encryption keys) SHALL come from environment variables and SHALL NOT be committed to source control.
5. Login SHALL be protected by rate limiting and temporary lockout (R1.8, R1.9).
6. Any password a person chooses SHALL be 12–128 characters, SHALL NOT be a known common password or use fewer than 5 different characters, and SHALL NOT contain the name part of the person's email address. There are no "must include a symbol" rules (NIST SP 800-63B). Generated temporary passwords are exempt. Email checks cover the format only; proving an address belongs to someone needs a confirmation email, which is out of scope (D2).

### N2: Integrity

**Sources:** Security and Privacy Architecture (integrity)

1. All SQL SHALL use parameterized queries; user input SHALL never be concatenated into SQL.
2. All input SHALL be validated on the server, whether or not the page also validates it.
3. Every state-changing form SHALL be protected by a CSRF token.
4. The database SHALL enforce integrity with NOT NULL, UNIQUE, and foreign-key constraints as defined in the ERD.
5. Concurrent requests SHALL NOT be able to overdraw a cash balance or oversell a position (for example, balance checks and updates happen under a row lock in the same transaction).

### N3: Availability and operations

**Sources:** Security and Privacy Architecture (availability); Infrastructure Architecture (network topology)

1. Production SHALL run under a production WSGI server (Gunicorn), not the Flask development server.
2. The web tier SHALL be stateless, so it can run on multiple servers behind a load balancer; session state SHALL NOT depend on a single server's memory.
3. The system SHALL target 99.9% availability in the production (AWS) deployment. *(D28: this is the cloud stage's target; the first, self-hosted stage is one container without failover.)*
4. Unexpected errors SHALL show a friendly message and SHALL NOT expose stack traces, SQL, or internal details.
5. The database SHALL be backed up using the 3-2-1 method (three copies, two media types, one off-site).
6. Servers SHALL NOT run as root, and the application's database account SHALL have only the privileges it needs.
7. A firewall SHALL sit between the internet and the load balancer. *(D28: self-hosted, Cloudflare's edge and WAF sit in front of the tunnel, and the container accepts no inbound connections; in the cloud, AWS WAF on the ALB.)*
8. The database SHALL NOT be reachable from the internet; only the application servers and the Admin Workstation may connect to it.

### N6: Performance

1. Under normal load, a confirmed trade or a submitted transfer SHALL complete, or fail and roll back, within 5 seconds.

### N4: Data precision

**Sources:** ERD

1. Money SHALL be stored and calculated as exact decimals to the cent (`DECIMAL(10,2)`), never floating point.
2. Share quantities SHALL be stored to 6 decimal places (`DECIMAL(18,6)`), allowing fractional shares.
3. Created and event timestamps SHALL have millisecond precision (`DATETIME(3)`).

### N5: Technology constraints

**Sources:** Programming section

1. The backend SHALL be Python with Flask; pages SHALL be server-rendered with Jinja templates.
2. Styling SHALL use Bootstrap; client-side behaviour SHALL use plain JavaScript.
3. Data SHALL be stored in MySQL.

---

## Assumptions

Defaults chosen where the SAD is silent. Build these unless the team changes them.

| ID | Assumption |
|---|---|
| A1 | Lockout: 5 consecutive failed logins lock the account for 15 minutes, then it unlocks automatically. (There is no reset flow, so a lock must expire on its own.) |
| A2 | Stay Signed In keeps the session for 30 days. |
| A3 | Unit is either Dollars (quantity = amount ÷ price) or Shares (total = quantity × price). |
| A4 | Order Type supports only Market, and Timing supports only Day Only, matching the wireframe defaults. Orders fill immediately at the current price. |
| A5 | Transfer Date must be today's date; transfers complete immediately, as the process maps show. |
| A6 | Customers can see only their own records; administrators can see all records on the History page. |
| A7 | The price generator runs every 30–120 seconds (random). Prices move like an individual large US stock: about 1.5% per trading day (one standard deviation, roughly 24% a year), equally likely up or down, at any price level and however often the generator runs. No single run moves a price more than 1%. All values are configurable. |
| A8 | "Previous Close" is the prior trading day's close; "Open" is today's open price. |
| A9 | Randomize History generates one year of past trading days (weekdays excluding holidays) with the same daily volatility as live prices (A7). With the toggle off, the stock starts with only today's price row. |
| A10 | Account numbers are system-generated unique 10-digit numeric strings. |
| A11 | Create Portfolio is started from a button in the Portfolios panel on the Summary page (no wireframe exists for it). |
| A12 | History date range options: Last 30 Days, Last 3 Months, Last 6 Months (default), Last 12 Months, Year to Date. |
| A13 | Trade page Search History keeps the customer's last 5 symbols in the session only; it is not stored in the database (there is no ERD table for it). |
| A14 | Top Gainers lists the 5 stocks with the highest % change since the previous close. |
| A15 | Stock Description is limited to 50 characters in the form, per the wireframe annotation (the ERD column allows 500). |
| A16 | Holidays follow the NYSE calendar dates, applied to Arizona-time trading days (D4, D6). |
| A17 | Prices count as stale when the market is open and no price update has happened for 5 minutes (more than twice the longest generator interval in A7). |
| A18 | Temporary passwords expire 7 days after they are issued (configurable). *(Decided by the team, D10.)* |
| A19 | Trending Stocks counts each stock's filled trades (buys and sells) since the start of today, Arizona time; the top 5 are shown, ties broken by symbol, and stocks with no trades today are left out. |
| A20 | Support contact details come from configuration (SUPPORT_EMAIL, SUPPORT_HOURS) so they can change without a code change. |

## Open questions

None. All questions raised by the SAD have been answered by the team and recorded as decisions (Q1 → D11, Q2 → D16, Q3 → D12, Q4 → D17, Q5 → D18, Q6 → D14, Q7 → D19, Q8 → D20, Q9 → D21, Q10 → D15, Q11 → D22, Q12 → D23, Q13 → D24). New questions go here, with the criteria they affect.
---

## Traceability

### SAD requirements to this document

| SAD ID | Summary | Covered by |
|---|---|---|
| CR01 | Buy when market open | R8.4 |
| CR02 | Sell when market open | R9.3 |
| CR03 | Verify sufficient funds before purchase | R8.5 |
| CR04 | Show confirmation before completing a trade | R8.6, R9.5 |
| CR05 | No cancelling after confirmation | R8.9, R9.8 |
| CR06 | Deduct purchase amount from cash | R8.7 |
| CR07 | Add sale proceeds to cash | R9.6 |
| CR08 | Return sold shares to available inventory | R9.6 |
| CR09 | Show portfolio and cash balance | R5, R6 |
| CR10 | Show transaction history | R13 |
| CR11 | Deposit cash | R11 |
| CR12 | Withdraw available cash | R12 |
| AR01 | Only admins maintain stock inventory | R2.4, R14.1 |
| AR02 | Admins create stocks | R14.2, R14.5 |
| AR03 | Company name, ticker, shares, initial price required | R14.2 (shares: D16) |
| AR04 | Admins modify market schedule | R15.3, R15.4 |
| AR05 | Default Mon–Fri 9–5, excluding holidays | R15.1, R15.2 |
| SR01 | Generate a price for each stock after creation | R14.5, R14.6 |
| SR02 | Update prices gradually during market hours | R16.1–R16.3 |
| SR03 | Hold prices while market is closed | R16.5 |
| SR04 | Show current price of each stock | R7.5 |
| SR05 | Prevent trades when market closed | R8.4, R9.3 |
| SR06 | Trades only during market hours | R8.4, R9.3 |
| SR07 | Update portfolio after each transaction | R8.7, R9.6 |
| SR08 | Update order history after each transaction | R8.7, R9.6, R13, R17.1 |
| SR09 | Timestamp account creation | R3.4 |
| SR10 | Timestamp each stock transaction | R8.7, R9.6 |
| SR11 | Timestamp each cash transaction | R11.4, R12.4 |

### SAD diagrams to this document

| SAD artifact | Covered by |
|---|---|
| PM Login | R1, R18 |
| PM Create Portfolio | R4 |
| PM Add Bank Account | R10 |
| PM Deposit Cash | R11 |
| PM Withdraw Cash | R12 |
| PM Buy Stock | R8 |
| PM Sell Stock | R9 |
| PM View Transaction History | R13 |
| PM Add Stock | R14 |
| PM Random Stock Price Generator | R16 |
| WF1 Stock Search | R7 |
| WF2 Stock Trading | R8, R9 |
| WF3 Summary | R4, R5 |
| WF4 Positions | R6 |
| WF5 Log in | R1, R18 |
| WF6 Administration | R3, R14, R15 |
| WF7 Fund Transfer | R10, R11, R12 |
| WF8 History | R13 |
| ERD | R3–R17, N4 |
| Network topology | N3 |
| Security and Privacy Architecture | R1, R2, R17, N1–N3 |
| Programming | N5 |
