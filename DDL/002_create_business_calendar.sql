-- 営業日カレンダー（business_calendar）TBL-002
-- データ設計書4.2節。source は祝日の再取込が手動登録を上書きしないために持つ（DBD-016）。
CREATE TABLE business_calendar (
    calendar_date TEXT PRIMARY KEY
        CHECK (calendar_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    is_business_day INTEGER NOT NULL CHECK (is_business_day IN (0, 1)),
    source TEXT NOT NULL CHECK (source IN ('自動取込', '手動登録')),
    note TEXT,
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    )
) STRICT;
