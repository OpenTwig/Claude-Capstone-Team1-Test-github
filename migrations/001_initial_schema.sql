-- 001: initial schema (docs/design.md, "Schema").
-- Table and column names follow the ERD exactly. `User`, `Transaction`, and the
-- column `timestamp` are MySQL keywords, so they are always backtick-quoted.
-- Connections run with time_zone '+00:00', so CURRENT_TIMESTAMP(3) is UTC (DD7).
-- Rule for migration files: every statement ends with ";" (see app/migrate.py).

CREATE TABLE `User` (
  userID              INT AUTO_INCREMENT PRIMARY KEY,
  name                VARCHAR(255) NOT NULL,
  date_of_birth       DATE         NOT NULL,
  ssn                 VARCHAR(255) NOT NULL,              -- Fernet ciphertext (DD8), never displayed (D8)
  email               VARCHAR(320) NOT NULL UNIQUE,       -- stored lower-cased
  password_hash       VARCHAR(255) NOT NULL,
  signup              DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  is_active           BOOLEAN      NOT NULL DEFAULT TRUE, -- DV1
  failed_login_count  INT          NOT NULL DEFAULT 0,    -- DV2
  locked_until        DATETIME(3)  NULL                   -- DV2
);

CREATE TABLE Admin (
  adminID       INT AUTO_INCREMENT PRIMARY KEY,
  userID        INT          NOT NULL UNIQUE,
  access_level  VARCHAR(20)  NOT NULL,
  department    VARCHAR(100) NULL,
  FOREIGN KEY (userID) REFERENCES `User`(userID)
);

CREATE TABLE BankAccount (
  bankAccountID   INT AUTO_INCREMENT PRIMARY KEY,
  userID          INT          NOT NULL,
  bank_name       VARCHAR(100) NOT NULL,
  nickname        VARCHAR(100) NOT NULL,
  account_number  VARCHAR(255) NOT NULL,                  -- Fernet ciphertext (DD8)
  routing_number  CHAR(9)      NOT NULL,
  created_at      DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  FOREIGN KEY (userID) REFERENCES `User`(userID),
  CHECK (routing_number REGEXP '^[0-9]{9}$')
);

CREATE TABLE Portfolio (
  portfolioID     INT AUTO_INCREMENT PRIMARY KEY,
  userID          INT           NOT NULL,
  cash_balance    DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  created_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  account_name    VARCHAR(100)  NOT NULL,
  account_type    VARCHAR(20)   NOT NULL,                 -- allowed values: Q6
  account_number  VARCHAR(20)   NOT NULL UNIQUE,
  FOREIGN KEY (userID) REFERENCES `User`(userID),
  CHECK (cash_balance >= 0)
);

CREATE TABLE Issuer (
  issuerID          INT AUTO_INCREMENT PRIMARY KEY,
  company_name      VARCHAR(255)  NOT NULL,
  ticker_symbol     VARCHAR(10)   NOT NULL UNIQUE,
  exchange          VARCHAR(10)   NULL,
  current_price     DECIMAL(10,2) NOT NULL,
  shares_available  DECIMAL(18,6) NULL,                   -- unused until Q2
  description       VARCHAR(500)  NULL,
  CHECK (current_price >= 0.01),
  CHECK (REGEXP_LIKE(ticker_symbol, '^[A-Z0-9]{1,10}$', 'c'))
);

CREATE TABLE StockPrice (
  priceID      INT AUTO_INCREMENT PRIMARY KEY,
  issuerID     INT           NOT NULL,
  price_date   DATE          NOT NULL,                    -- Arizona trading date
  open_price   DECIMAL(10,2) NOT NULL,
  close_price  DECIMAL(10,2) NOT NULL,
  FOREIGN KEY (issuerID) REFERENCES Issuer(issuerID),
  UNIQUE KEY u_issuer_date (issuerID, price_date)         -- R16.7
);

CREATE TABLE Inventory (
  inventoryID   INT AUTO_INCREMENT PRIMARY KEY,
  portfolioID   INT           NOT NULL,
  issuerID      INT           NOT NULL,
  quantity      DECIMAL(18,6) NOT NULL,
  average_cost  DECIMAL(10,2) NOT NULL,
  reinvest      BOOLEAN       NOT NULL DEFAULT FALSE,     -- unused until Q11
  FOREIGN KEY (portfolioID) REFERENCES Portfolio(portfolioID),
  FOREIGN KEY (issuerID)    REFERENCES Issuer(issuerID),
  UNIQUE KEY u_portfolio_issuer (portfolioID, issuerID),
  CHECK (quantity > 0)                                    -- zero rows are deleted
);

CREATE TABLE `Transaction` (
  transactionID    INT AUTO_INCREMENT PRIMARY KEY,
  portfolioID      INT           NOT NULL,
  issuerID         INT           NOT NULL,
  type             VARCHAR(4)    NOT NULL,
  quantity         DECIMAL(18,6) NOT NULL,
  price_per_share  DECIMAL(10,2) NOT NULL,
  total_amount     DECIMAL(10,2) NOT NULL,
  status           VARCHAR(20)   NOT NULL,
  `timestamp`      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  order_type       VARCHAR(10)   NOT NULL,
  timing           VARCHAR(10)   NOT NULL,
  FOREIGN KEY (portfolioID) REFERENCES Portfolio(portfolioID),
  FOREIGN KEY (issuerID)    REFERENCES Issuer(issuerID),
  KEY k_portfolio_time (portfolioID, `timestamp`),
  CHECK (type IN ('Buy', 'Sell')),
  CHECK (status IN ('Filled')),
  CHECK (order_type IN ('Market')),                       -- A4
  CHECK (timing IN ('Day Only')),                         -- A4
  CHECK (quantity > 0 AND total_amount > 0)
);

CREATE TABLE Transfer (
  transferID        INT AUTO_INCREMENT PRIMARY KEY,
  from_portfolioID  INT           NULL,
  to_portfolioID    INT           NULL,
  bankAccountID     INT           NULL,
  amount            DECIMAL(10,2) NOT NULL,
  transfer_date     DATE          NOT NULL,
  status            VARCHAR(20)   NOT NULL,
  created_at        DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  transfer_type     VARCHAR(10)   NOT NULL,
  FOREIGN KEY (from_portfolioID) REFERENCES Portfolio(portfolioID),
  FOREIGN KEY (to_portfolioID)   REFERENCES Portfolio(portfolioID),
  FOREIGN KEY (bankAccountID)    REFERENCES BankAccount(bankAccountID),
  CHECK (amount > 0),
  CHECK (status IN ('Pending', 'Completed')),
  CHECK (
    (transfer_type = 'Deposit'    AND bankAccountID IS NOT NULL AND to_portfolioID IS NOT NULL AND from_portfolioID IS NULL) OR
    (transfer_type = 'Withdrawal' AND bankAccountID IS NOT NULL AND from_portfolioID IS NOT NULL AND to_portfolioID IS NULL)
  )                                                       -- portfolio-to-portfolio: Q10
);

CREATE TABLE History (
  historyID      INT AUTO_INCREMENT PRIMARY KEY,
  portfolioID    INT          NOT NULL,
  transactionID  INT          NULL UNIQUE,
  transferID     INT          NULL UNIQUE,
  action         VARCHAR(50)  NOT NULL,
  `timestamp`    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  FOREIGN KEY (portfolioID)   REFERENCES Portfolio(portfolioID),
  FOREIGN KEY (transactionID) REFERENCES `Transaction`(transactionID),
  FOREIGN KEY (transferID)    REFERENCES Transfer(transferID),
  KEY k_portfolio_time (portfolioID, `timestamp`),
  CHECK ((transactionID IS NULL) <> (transferID IS NULL)) -- exactly one (R17.1)
);

-- DV3: admin-set market hours (AR04)
CREATE TABLE MarketSchedule (
  scheduleID  INT AUTO_INCREMENT PRIMARY KEY,
  open_time   TIME        NOT NULL,                       -- Arizona local time
  close_time  TIME        NOT NULL,
  start_date  DATE        NULL,                           -- meaning pending Q9
  end_date    DATE        NULL,                           -- meaning pending Q9
  updated_by  INT         NOT NULL,
  updated_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  FOREIGN KEY (updated_by) REFERENCES `User`(userID),
  CHECK (close_time > open_time)
);

-- DV4: generator heartbeat for the stale-price indicator (A17)
CREATE TABLE SystemState (
  state_key    VARCHAR(50)  PRIMARY KEY,                  -- e.g. 'last_price_tick'
  state_value  VARCHAR(255) NOT NULL,
  updated_at   DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
);
