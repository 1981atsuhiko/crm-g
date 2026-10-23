-- 賞与（bonus）TBL-014
-- データ設計書6.1節。bonus_rate への外部キーは張らない（DBD-020）。
CREATE TABLE bonus (
    employee_id INTEGER NOT NULL REFERENCES employee (id) ON DELETE RESTRICT,
    eval_half TEXT NOT NULL CHECK (eval_half GLOB '[0-9][0-9][0-9][0-9]-H[12]'),
    pay_month TEXT NOT NULL CHECK (
        pay_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
        AND substr(pay_month, 6, 2) IN ('04', '10')
    ),
    base_type TEXT NOT NULL CHECK (base_type IN ('基本給', '月給合計')),
    base_amount INTEGER NOT NULL CHECK (base_amount >= 0),
    eval_rank TEXT CHECK (eval_rank IS NULL OR eval_rank IN ('S', 'A', 'B', 'C', 'D')),
    rate_bp INTEGER CHECK (rate_bp IS NULL OR rate_bp >= 0),
    bonus_amount INTEGER NOT NULL CHECK (bonus_amount >= 0),
    source TEXT NOT NULL CHECK (source IN ('取込', '手入力', 'アプリ計算')),
    is_closed INTEGER NOT NULL DEFAULT 0 CHECK (is_closed IN (0, 1)),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    PRIMARY KEY (employee_id, eval_half),
    -- source が取込でないときは、eval_rank・rate_bp がNULLでない（5.1節）。
    CHECK (source = '取込' OR (eval_rank IS NOT NULL AND rate_bp IS NOT NULL))
) STRICT;

CREATE INDEX bonus_pay_month_idx ON bonus (pay_month);
