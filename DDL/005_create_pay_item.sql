-- 給与項目・手当マスタ（pay_item）TBL-005
-- データ設計書4.5節。deduction_order は1以上、NULLを除いて一意。
-- in_monthly_salary と cost_direct が同時に1にならない検査は、データ設計書4.5節で
-- 確認が必要（TBD）とされているため、CHECK制約にはしていない（意図的な未実装）。
CREATE TABLE pay_item (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('基本給', '固定支給', '一時手当')),
    in_monthly_salary INTEGER NOT NULL CHECK (in_monthly_salary IN (0, 1)),
    cost_direct INTEGER NOT NULL CHECK (cost_direct IN (0, 1)),
    deduction_order INTEGER UNIQUE CHECK (deduction_order IS NULL OR deduction_order >= 1),
    sort_order INTEGER NOT NULL,
    valid_from TEXT NOT NULL
        CHECK (valid_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    valid_to TEXT
        CHECK (valid_to IS NULL OR valid_to GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
) STRICT;
