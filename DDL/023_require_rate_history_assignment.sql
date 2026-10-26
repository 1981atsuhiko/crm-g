-- 単価履歴（rate_history）TBL-017 の assignment_id を必須にする
-- データ設計書 v0.7 の 7.3 節・DBD-018（単価は必ず参画に紐づける。ユーザー承認 2026-10-26）。
-- SQLite は列の NOT NULL を ALTER TABLE で変えられないため、表を作り直す。
-- assignment_id が NULL の行が残っていると、INSERT が NOT NULL 制約で失敗し、
-- マイグレーション全体が中止される（適用前のバックアップから戻せる）。
-- 作り直しの途中で失敗したときに作りかけの表が残らないよう、1つのトランザクションにする。
BEGIN;

CREATE TABLE rate_history_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employee (id) ON DELETE RESTRICT,
    assignment_id INTEGER NOT NULL REFERENCES assignment (id) ON DELETE RESTRICT,
    effective_from TEXT NOT NULL
        CHECK (effective_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    unit_price INTEGER NOT NULL CHECK (unit_price >= 0),
    revision_basis TEXT,
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

INSERT INTO rate_history_new
    (id, employee_id, assignment_id, effective_from, unit_price, revision_basis, note,
     created_at, updated_at)
SELECT id, employee_id, assignment_id, effective_from, unit_price, revision_basis, note,
       created_at, updated_at
FROM rate_history;

DROP TABLE rate_history;
ALTER TABLE rate_history_new RENAME TO rate_history;

CREATE UNIQUE INDEX rate_history_unique ON rate_history (assignment_id, effective_from);
CREATE INDEX rate_history_employee_effective_idx ON rate_history (employee_id, effective_from);

COMMIT;
