-- 計算設定（system_setting）TBL-006
-- データ設計書4.6節。value_integer と value_text は、どちらか一方だけがNULLでない。
CREATE TABLE system_setting (
    setting_key TEXT NOT NULL CHECK (setting_key IN ('原価係数', '端数処理')),
    valid_from TEXT NOT NULL
        CHECK (valid_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    valid_to TEXT
        CHECK (valid_to IS NULL OR valid_to GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    value_integer INTEGER,
    value_text TEXT,
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    PRIMARY KEY (setting_key, valid_from),
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CHECK ((value_integer IS NULL) <> (value_text IS NULL))
) STRICT;
