"""SQL injection payloads (task 5.1; N2.1, N2.2). Every one goes through every text input.

If any of these reached the SQL text instead of a bound parameter, it would log someone in, return
extra rows, change the schema, or stall the request (SLEEP), which the tests check for.
"""

PAYLOADS: dict[str, str] = {
    "or_true": "' OR '1'='1' -- ",
    "drop_table": "'; DROP TABLE `User`; --",
    "double_quote_or": '" OR ""="',
    "union_select": "1 UNION SELECT email, password_hash FROM `User` --",
    "comment_hash": "admin'#",
    "sleep": "' OR SLEEP(5) -- ",
    "percent": "%",
    "underscore": "_",
    "backslashes": "\\\\'\\",
    "nul_byte": "a\x00' OR 1=1 --",
    "lookalike_quotes": "ʼ OR ʼ1ʼ=ʼ1ʼ -- ＇ OR ＇1＇=＇1",   # modifier-letter and full-width apostrophes
    "long_10000": "A" * 10_000,
}

SLOW_SECONDS = 2.0  # a response slower than this suggests SLEEP() ran in the database
