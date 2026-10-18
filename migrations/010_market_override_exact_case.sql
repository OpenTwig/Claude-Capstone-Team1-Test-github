-- 010: market_override compares exactly (DV12; task 6.3). Under the default case-insensitive collation the CHECK
-- from migration 009 let 'open' or 'CLOSED' through, which the app can't read back. A binary collation makes the
-- CHECK accept only 'Schedule', 'Open', and 'Closed'.

ALTER TABLE MarketSchedule
  MODIFY market_override VARCHAR(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL DEFAULT 'Schedule';
