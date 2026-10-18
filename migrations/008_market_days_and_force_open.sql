-- 008: the Market Time form also picks the trading days and can force the market open (D29; design DV12; task 6.3).
-- trading_days is a bit mask of date.weekday() numbers: bit 0 = Monday ... bit 6 = Sunday. 31 = Monday to Friday,
-- so existing schedules keep behaving exactly as before.

ALTER TABLE MarketSchedule
  ADD COLUMN trading_days TINYINT UNSIGNED NOT NULL DEFAULT 31 AFTER close_time,
  ADD COLUMN force_open BOOLEAN NOT NULL DEFAULT FALSE AFTER trading_days,
  ADD CONSTRAINT market_schedule_days CHECK (trading_days BETWEEN 1 AND 127);
