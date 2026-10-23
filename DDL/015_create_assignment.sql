-- 参画（assignment）TBL-015
-- データ設計書7.1節。
CREATE TABLE assignment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employee (id) ON DELETE RESTRICT,
    customer_name TEXT NOT NULL,
    project_name TEXT,
    category TEXT NOT NULL CHECK (category IN ('SES', '受託', '既存アカプラ', '教育原価')),
    start_date TEXT NOT NULL
        CHECK (start_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    end_date TEXT
        CHECK (end_date IS NULL OR end_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
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

CREATE INDEX assignment_employee_idx ON assignment (employee_id);
