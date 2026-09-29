-- DV5, D23: each portfolio's value, saved once per trading day after the close by the price
-- generator, for the value charts and YTD change.
CREATE TABLE PortfolioValueDaily (
  portfolioID  INT           NOT NULL,
  value_date   DATE          NOT NULL,                    -- Arizona trading date
  cash         DECIMAL(10,2) NOT NULL,
  total_value  DECIMAL(12,2) NOT NULL,                    -- cash + positions at the close
  PRIMARY KEY (portfolioID, value_date),
  FOREIGN KEY (portfolioID) REFERENCES Portfolio(portfolioID)
);