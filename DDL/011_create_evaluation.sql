-- 評価（evaluation）TBL-011
-- データ設計書5.3節。evaluator は社員に紐づけない自由入力（画面設計書 UID-077。暫定）。
CREATE TABLE evaluation (
    employee_id INTEGER NOT NULL REFERENCES employee (id) ON DELETE RESTRICT,
    eval_half TEXT NOT NULL CHECK (eval_half GLOB '[0-9][0-9][0-9][0-9]-H[12]'),
    eval_rank TEXT NOT NULL CHECK (eval_rank IN ('S', 'A', 'B', 'C', 'D')),
    evaluator TEXT,
    comment TEXT,
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    PRIMARY KEY (employee_id, eval_half)
) STRICT;
