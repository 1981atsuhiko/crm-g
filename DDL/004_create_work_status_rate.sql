-- 稼働状態の給与支給率（work_status_rate）TBL-004
-- データ設計書4.4節。有効期間付き（DBD-015）。期間の重複はアプリケーション層で守る（10章-9）。
CREATE TABLE work_status_rate (
    work_status_code TEXT NOT NULL REFERENCES work_status (code) ON DELETE RESTRICT,
    valid_from TEXT NOT NULL
        CHECK (valid_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    valid_to TEXT
        CHECK (valid_to IS NULL OR valid_to GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    payroll_rate_bp INTEGER NOT NULL CHECK (payroll_rate_bp >= 0),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    PRIMARY KEY (work_status_code, valid_from),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
) STRICT;
