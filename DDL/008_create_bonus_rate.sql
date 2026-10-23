-- 評価ランクの掛け率（bonus_rate）TBL-008
-- データ設計書4.8節。
CREATE TABLE bonus_rate (
    eval_half TEXT NOT NULL CHECK (eval_half GLOB '[0-9][0-9][0-9][0-9]-H[12]'),
    eval_rank TEXT NOT NULL CHECK (eval_rank IN ('S', 'A', 'B', 'C', 'D')),
    rate_bp INTEGER NOT NULL CHECK (rate_bp >= 0),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    PRIMARY KEY (eval_half, eval_rank)
) STRICT;
