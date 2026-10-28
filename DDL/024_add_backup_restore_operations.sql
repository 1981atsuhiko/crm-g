-- 操作ログ（operation_log）TBL-020 の operation に「バックアップ作成」「復元」を加える
-- 画面設計書 SCN-065（14章-11）。バックアップの作成と復元を操作ログに残す（ユーザー承認 2026-10-27）。
-- SQLite は CHECK 制約を ALTER TABLE で変えられないため、表を作り直す。
-- id はそのまま移し、AUTOINCREMENT の採番（sqlite_sequence）も引き継ぐ。
-- 追記専用のトリガー（DBD-019）と索引は、表の削除で消えるため作り直す。
-- 作り直しの途中で失敗したときに作りかけの表が残らないよう、1つのトランザクションにする。
BEGIN;

CREATE TABLE operation_log_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL CHECK (
        occurred_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    operation TEXT NOT NULL CHECK (
        operation IN (
            '閲覧', '出力', '登録', '更新', '削除',
            '月次締め', '月次締め解除', '取込', '認証成功', '認証失敗', 'パスコード設定',
            'バックアップ作成', '復元'
        )
    ),
    target_table TEXT NOT NULL,
    target_key TEXT,
    detail TEXT,
    before_value TEXT CHECK (before_value IS NULL OR json_valid(before_value)),
    after_value TEXT CHECK (after_value IS NULL OR json_valid(after_value))
) STRICT;

INSERT INTO operation_log_new
    (id, occurred_at, operation, target_table, target_key, detail, before_value, after_value)
SELECT id, occurred_at, operation, target_table, target_key, detail, before_value, after_value
FROM operation_log;

-- 採番の続き（これまでに使った最大の id）を引き継ぐ。移した行の最大の id だけでなく、
-- 元の表の sqlite_sequence の値を使う（取り消された追加で進んだ番号を再利用しないため）。
DELETE FROM sqlite_sequence WHERE name = 'operation_log_new';
INSERT INTO sqlite_sequence (name, seq)
SELECT 'operation_log_new', seq FROM sqlite_sequence WHERE name = 'operation_log';

DROP TABLE operation_log;
ALTER TABLE operation_log_new RENAME TO operation_log;

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

COMMIT;
