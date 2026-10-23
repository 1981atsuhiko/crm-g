-- 単価履歴（rate_history）TBL-017
-- データ設計書7.3節。一意：employee_id・assignment_id・effective_fromの組。
-- SQLiteの一意制約はNULLを別の値として扱うため、COALESCE(assignment_id, 0)の
-- 式インデックスで一意にする（データ設計書7.3節の記載どおり）。
CREATE TABLE rate_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employee (id) ON DELETE RESTRICT,
    assignment_id INTEGER REFERENCES assignment (id) ON DELETE RESTRICT,
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

CREATE UNIQUE INDEX rate_history_unique
    ON rate_history (employee_id, COALESCE(assignment_id, 0), effective_from);
CREATE INDEX rate_history_employee_effective_idx ON rate_history (employee_id, effective_from);
