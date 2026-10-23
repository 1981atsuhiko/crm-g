-- 給与履歴（salary_history）TBL-009
-- データ設計書5.1節。期間の重複はアプリケーション層で守る（10章-1）。
CREATE TABLE salary_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employee (id) ON DELETE RESTRICT,
    effective_from TEXT NOT NULL
        CHECK (effective_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    effective_to TEXT
        CHECK (effective_to IS NULL OR effective_to GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    pay_system TEXT NOT NULL CHECK (pay_system IN ('通常評価制度', '年俸制', '単価連動')),
    employment_type TEXT NOT NULL CHECK (employment_type IN ('正社員', '契約社員')),
    grade TEXT,
    annual_salary INTEGER CHECK (annual_salary IS NULL OR annual_salary >= 0),
    revision_type TEXT NOT NULL CHECK (
        revision_type IN ('定期（4月）', '定期（10月）', '年俸改定（4月）', '制度移行', '入社', 'その他')
    ),
    note TEXT,
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    UNIQUE (employee_id, effective_from),
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    -- annual_salary は、pay_system が年俸制のときだけNULLでない（5.1節）。
    CHECK ((pay_system = '年俸制') = (annual_salary IS NOT NULL))
) STRICT;
