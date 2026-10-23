-- 稼働状態マスタ（work_status）TBL-003
-- データ設計書4.3節。
CREATE TABLE work_status (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_billable INTEGER NOT NULL CHECK (is_billable IN (0, 1)),
    is_cost_target INTEGER NOT NULL CHECK (is_cost_target IN (0, 1)),
    legacy_name TEXT,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    )
) STRICT;
