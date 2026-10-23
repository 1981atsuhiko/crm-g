-- 稼働期間（work_period）TBL-016
-- データ設計書7.2節。稼働中（売上計上1）の状態はassignment_idを持つ前提だが、
-- assignment_idの必須条件は稼働状態マスタに依存するため、アプリケーション層で守る（10章-3）。
CREATE TABLE work_period (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employee (id) ON DELETE RESTRICT,
    start_date TEXT NOT NULL
        CHECK (start_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    end_date TEXT
        CHECK (end_date IS NULL OR end_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    work_status_code TEXT NOT NULL REFERENCES work_status (code) ON DELETE RESTRICT,
    assignment_id INTEGER REFERENCES assignment (id) ON DELETE RESTRICT,
    billing_ratio_bp INTEGER NOT NULL DEFAULT 10000
        CHECK (billing_ratio_bp >= 0 AND billing_ratio_bp <= 10000),
    note TEXT,
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    CHECK (end_date IS NULL OR end_date >= start_date)
) STRICT;

CREATE INDEX work_period_employee_start_idx ON work_period (employee_id, start_date);
