-- 月次の給与計算結果の内訳（monthly_payroll_item）TBL-013
-- データ設計書5.5節。項目の列構成は暫定（TBD）。
CREATE TABLE monthly_payroll_item (
    employee_id INTEGER NOT NULL,
    year_month TEXT NOT NULL,
    pay_item_code TEXT NOT NULL REFERENCES pay_item (code) ON DELETE RESTRICT,
    paid_amount INTEGER NOT NULL CHECK (paid_amount >= 0),
    deduction_amount INTEGER NOT NULL CHECK (deduction_amount >= 0),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    PRIMARY KEY (employee_id, year_month, pay_item_code),
    FOREIGN KEY (employee_id, year_month)
        REFERENCES monthly_payroll (employee_id, year_month) ON DELETE RESTRICT
) STRICT;
