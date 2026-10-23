-- 給与の内訳（salary_history_item）TBL-010
-- データ設計書5.2節。
CREATE TABLE salary_history_item (
    salary_history_id INTEGER NOT NULL REFERENCES salary_history (id) ON DELETE RESTRICT,
    pay_item_code TEXT NOT NULL REFERENCES pay_item (code) ON DELETE RESTRICT,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    PRIMARY KEY (salary_history_id, pay_item_code)
) STRICT;
