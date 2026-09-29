-- 003: limited share supply and delisting (D16, D20; design DV9; task 2.3).
--   shares_available  required and never negative: a buy can't take more than exist (AR03, CR08)
--   is_listed         FALSE once an administrator delists the stock: no longer searchable or buyable,
--                     but customers who hold it can still sell

UPDATE Issuer SET shares_available = 0 WHERE shares_available IS NULL;

ALTER TABLE Issuer
  MODIFY shares_available DECIMAL(18,6) NOT NULL,
  ADD COLUMN is_listed BOOLEAN NOT NULL DEFAULT TRUE,
  ADD CONSTRAINT issuer_shares_not_negative CHECK (shares_available >= 0);
