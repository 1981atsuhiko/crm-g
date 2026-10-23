-- 月次の売上・原価（monthly_pl）TBL-019
-- データ設計書7.5節。
CREATE TABLE monthly_pl (
    employee_id INTEGER NOT NULL REFERENCES employee (id) ON DELETE RESTRICT,
    year_month TEXT NOT NULL CHECK (year_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    category TEXT CHECK (category IS NULL OR category IN ('SES', '受託', '既存アカプラ', '教育原価')),
    sales INTEGER NOT NULL CHECK (sales >= 0),
    cost_salary INTEGER NOT NULL CHECK (cost_salary >= 0),
    cost_allowance INTEGER NOT NULL CHECK (cost_allowance >= 0),
    cost_bonus INTEGER NOT NULL CHECK (cost_bonus >= 0),
    cost_total INTEGER NOT NULL CHECK (cost_total >= 0),
    gross_profit INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('取込', '再計算', 'アプリ計算')),
    is_closed INTEGER NOT NULL DEFAULT 0 CHECK (is_closed IN (0, 1)),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    PRIMARY KEY (employee_id, year_month),
    -- cost_total・gross_profit の整合性（データ設計書7.5節）。
    CHECK (cost_total = cost_salary + cost_allowance + cost_bonus),
    CHECK (gross_profit = sales - cost_total)
) STRICT;

CREATE INDEX monthly_pl_year_month_idx ON monthly_pl (year_month);
