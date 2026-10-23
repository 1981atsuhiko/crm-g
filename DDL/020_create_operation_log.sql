-- 操作ログ（operation_log）TBL-020
-- データ設計書8.1節。追記専用とする（DBD-019）。created_at・updated_atは持たない（DBD-011）。
CREATE TABLE operation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL CHECK (
        occurred_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    operation TEXT NOT NULL CHECK (
        operation IN (
            '閲覧', '出力', '登録', '更新', '削除',
            '月次締め', '月次締め解除', '取込', '認証成功', '認証失敗', 'パスコード設定'
        )
    ),
    target_table TEXT NOT NULL,
    target_key TEXT,
    detail TEXT,
    before_value TEXT CHECK (before_value IS NULL OR json_valid(before_value)),
    after_value TEXT CHECK (after_value IS NULL OR json_valid(after_value))
) STRICT;

-- 追記専用（DBD-019）。UPDATE・DELETEをトリガーで拒否する。
CREATE TRIGGER operation_log_no_update
BEFORE UPDATE ON operation_log
BEGIN
    SELECT RAISE(ABORT, 'operation_log は追記専用のため、更新できません');
END;

CREATE TRIGGER operation_log_no_delete
BEFORE DELETE ON operation_log
BEGIN
    SELECT RAISE(ABORT, 'operation_log は追記専用のため、削除できません');
END;

CREATE INDEX operation_log_occurred_at_idx ON operation_log (occurred_at);
CREATE INDEX operation_log_target_idx ON operation_log (target_table, target_key);
