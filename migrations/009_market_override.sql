-- 009: the one-way Force market open switch becomes a three-way market control (D29; design DV12; task 6.3).
-- market_override is 'Schedule' (follow the hours and days), 'Open' (forced open), or 'Closed' (forced closed).
-- A row saved with force_open on carries over as 'Open'.

ALTER TABLE MarketSchedule
  ADD COLUMN market_override VARCHAR(10) NOT NULL DEFAULT 'Schedule' AFTER trading_days,
  ADD CONSTRAINT market_schedule_override CHECK (market_override IN ('Schedule', 'Open', 'Closed'));

UPDATE MarketSchedule SET market_override = 'Open' WHERE force_open;

ALTER TABLE MarketSchedule DROP COLUMN force_open;
