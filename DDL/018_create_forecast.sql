-- 見込み（forecast）TBL-018
-- データ設計書7.4節。
CREATE TABLE forecast (
    employee_id INTEGER NOT NULL REFERENCES employee (id) ON DELETE RESTRICT,
    year_month TEXT NOT NULL CHECK (year_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    confidence TEXT NOT NULL CHECK (confidence IN ('確定', '高', '低', '待機')),
    forecast_unit_price INTEGER CHECK (forecast_unit_price IS NULL OR forecast_unit_price >= 0),
    forecast_billing_ratio_bp INTEGER
        CHECK (
            forecast_billing_ratio_bp IS NULL
            OR (forecast_billing_ratio_bp >= 0 AND forecast_billing_ratio_bp <= 10000)
        ),
    created_at TEXT NOT NULL CHECK (
        created_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
    ),
    PRIMARY KEY (employee_id, year_month)
) STRICT;
