-- 002: first-login password change and admin-issued temporary passwords (D10, R18; design DV7).
--   must_change_password      the user is still on a temporary password
--   temp_password_expires_at  when that temporary password stops working (A18: 7 days)
--   session_version           part of every login's ID; bumping it ends the user's other logins

ALTER TABLE `User`
  ADD COLUMN must_change_password     BOOLEAN     NOT NULL DEFAULT FALSE,
  ADD COLUMN temp_password_expires_at DATETIME(3) NULL,
  ADD COLUMN session_version          INT         NOT NULL DEFAULT 0;

-- Existing customers (not administrators) were given temporary passwords, so they must change them.
UPDATE `User` u LEFT JOIN Admin a ON a.userID = u.userID
   SET u.must_change_password = TRUE,
       u.temp_password_expires_at = UTC_TIMESTAMP(3) + INTERVAL 7 DAY
 WHERE a.adminID IS NULL;
