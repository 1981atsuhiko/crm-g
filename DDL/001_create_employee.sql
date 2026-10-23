-- 社員（employee）TBL-001
-- データ設計書4.1節。email は小文字に正規化して保存する。
CREATE TABLE employee (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    employee_no TEXT,
    name TEXT NOT NULL,
    name_kana TEXT,
    employment_type TEXT
        CHECK (employment_type IS NULL OR employment_type IN ('正社員', '契約社員')),
    birth_date TEXT
        CHECK (birth_date IS NULL OR birth_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    -- 性別のDBの検査（3値）は、データ設計書4.1節で確認が必要（TBD）とされているため、
    -- employee_no の一意制約と同様、CHECK制約にはしていない（意図的な未実装）。
    gender TEXT,
    join_date TEXT
        CHECK (join_date IS NULL OR join_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    nearest_station TEXT,
    residence_area TEXT,
    job_type TEXT,
    enrollment_status TEXT NOT NULL
        CHECK (enrollment_status IN ('在籍', '退職予定', '退職', '契約満了', '移管')),
    retire_date TEXT
        CHECK (retire_date IS NULL OR retire_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    CHECK (email = lower(email) COLLATE BINARY),
    CHECK (retire_date IS NULL OR join_date IS NULL OR retire_date >= join_date)
) STRICT;

CREATE UNIQUE INDEX employee_email_unique ON employee (email COLLATE NOCASE);
