-- 月次の給与計算結果（monthly_payroll）TBL-012
-- データ設計書5.4節。monthly_salary = paid_amount + deduction はCHECKにしない
-- （成り立たない場合を check_result = '要確認' として保存するため。REQ-CAL-007）。
CREATE TABLE monthly_payroll (
    employee_id INTEGER NOT NULL REFERENCES employee (id) ON DELETE RESTRICT,
    year_month TEXT NOT NULL CHECK (year_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    monthly_salary INTEGER NOT NULL CHECK (monthly_salary >= 0),
    paid_amount INTEGER NOT NULL CHECK (paid_amount >= 0),
    deduction INTEGER NOT NULL CHECK (deduction >= 0),
    work_status_summary TEXT,
    hr_target TEXT NOT NULL DEFAULT '対象' CHECK (hr_target IN ('対象', '対象外')),
    hr_note TEXT,
    check_result TEXT NOT NULL CHECK (check_result IN ('OK', '要確認')),
    calculated_at TEXT NOT NULL CHECK (
        calculated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    is_closed INTEGER NOT NULL DEFAULT 0 CHECK (is_closed IN (0, 1)),
    closed_at TEXT CHECK (
        closed_at IS NULL OR closed_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    PRIMARY KEY (employee_id, year_month),
    -- closed_at は、is_closed が1のときだけNULLでない（DBD-017）。
    CHECK ((is_closed = 1) = (closed_at IS NOT NULL))
) STRICT;

CREATE INDEX monthly_payroll_year_month_idx ON monthly_payroll (year_month);
