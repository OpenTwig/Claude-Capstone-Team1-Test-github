-- DV11, D25: customers can remove a bank account. It is hidden, not deleted, so past transfers
-- keep pointing at it (R10.7, R17.2).
ALTER TABLE BankAccount ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE;
