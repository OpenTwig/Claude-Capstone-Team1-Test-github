-- 004: the Market Time form sets open and close times only (D21; design DV10; task 2.4).
-- The saved hours apply every trading day until changed, so the date columns go.

ALTER TABLE MarketSchedule
  DROP COLUMN start_date,
  DROP COLUMN end_date;
