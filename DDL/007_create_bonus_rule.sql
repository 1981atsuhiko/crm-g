-- 賞与ルール（bonus_rule）TBL-007
-- データ設計書4.7節。
CREATE TABLE bonus_rule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    valid_from TEXT NOT NULL
        CHECK (valid_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    valid_to TEXT
        CHECK (valid_to IS NULL OR valid_to GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    base_type TEXT NOT NULL CHECK (base_type IN ('基本給', '月給合計')),
    target_pay_system TEXT NOT NULL
        CHECK (target_pay_system IN ('通常評価制度', '年俸制', '単価連動')),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
) STRICT;
