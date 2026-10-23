-- パスコードの認証情報（app_credential）TBL-022
-- データ設計書8.3節。行がない状態を「パスコード未設定」とする。
CREATE TABLE app_credential (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    algorithm TEXT NOT NULL CHECK (algorithm = 'scrypt'),
    scrypt_n INTEGER NOT NULL CHECK (scrypt_n > 1),
    scrypt_r INTEGER NOT NULL CHECK (scrypt_r >= 1),
    scrypt_p INTEGER NOT NULL CHECK (scrypt_p >= 1),
    -- 16バイトを16進数にした32文字（小文字）。
    salt TEXT NOT NULL CHECK (length(salt) = 32 AND salt NOT GLOB '*[^0-9a-f]*'),
    -- 32バイトを16進数にした64文字（小文字）。
    hash TEXT NOT NULL CHECK (length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    locked_until TEXT CHECK (
        locked_until IS NULL OR locked_until GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    )
) STRICT;
