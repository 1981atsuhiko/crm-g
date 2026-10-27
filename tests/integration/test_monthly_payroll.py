"""月次給与計算の結果の表示と、人事連携の設定の試験（`SCN-032`）。

数値は計算仕様書 9章の EX-001（給与）・EX-005（売上）・EX-006（原価・粗利）による。
"""

import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.assignment import AssignmentInput, create_assignment
from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.monthly_close import close_month
from crm_g.application.monthly_payroll import (
    ClosedMonthError,
    InvalidHrTargetError,
    ResultNotFoundError,
    list_monthly_results,
    list_payroll_items,
    update_hr_settings,
)
from crm_g.application.recalculation import last_billable_unit_price, recalculate_month
from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.application.unit_price import RateHistoryInput, register_rate_history
from crm_g.application.work_period import WorkPeriodInput, create_work_period
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-25T00:00:00.000000Z"
_MONTH = "2026-11"


def _period(
    connection: sqlite3.Connection,
    start: date,
    end: date | None,
    status: str,
    assignment: int | None,
) -> None:
    create_work_period(
        connection,
        1,
        WorkPeriodInput(
            start_date=start,
            end_date=end,
            work_status_code=status,
            assignment_id=assignment,
            billing_ratio_percent="100",
            note=None,
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """EX-001 の社員（11/2〜11/9 は待機（減額あり）、11/10 以降は開発稼働・単価 800,000）。"""
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) VALUES "
            "(1, 'taro@example.com', '山田太郎', '在籍', '2020-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
        create_year_calendar(connection, 2026)
        register_salary_history(
            connection,
            1,
            SalaryHistoryInput(
                effective_from=date(2026, 4, 1),
                pay_system="通常評価制度",
                employment_type="正社員",
                grade=None,
                annual_salary=None,
                revision_type="定期（4月）",
                note=None,
                items=(
                    SalaryItemInput("BASE_SALARY", 200000),
                    SalaryItemInput("DUTY_ALLOWANCE", 50000),
                    SalaryItemInput("FIXED_OVERTIME", 30000),
                    SalaryItemInput("HOUSING", 10000),
                    SalaryItemInput("LEADER", 10000),
                ),
            ),
        )
        assignment = create_assignment(
            connection,
            1,
            AssignmentInput(
                customer_name="A社",
                project_name=None,
                category="SES",
                start_date=date(2026, 11, 1),
                end_date=None,
            ),
        )
        register_rate_history(
            connection,
            assignment,
            RateHistoryInput(
                effective_from=date(2026, 11, 1), unit_price=800000, revision_basis=None, note=None
            ),
        )
        _period(connection, date(2026, 10, 1), date(2026, 11, 9), "WAIT_CUT", None)
        _period(connection, date(2026, 11, 10), None, "DEV", assignment)
        recalculate_month(connection, "2026-10")
        recalculate_month(connection, _MONTH)
        connection.commit()
        yield connection


def test_tc_fun_011_results_of_ex001(connection: sqlite3.Connection) -> None:
    rows = list_monthly_results(connection, _MONTH)

    assert len(rows) == 1
    row = rows[0]
    assert (row.employee_name, row.monthly_salary, row.paid_amount, row.deduction) == (
        "山田太郎",
        300000,
        268422,
        31578,
    )
    # 基本給は区分が「基本給」の項目、手当はそれ以外の支給額の合計（画面設計書 SCN-032）。
    assert (row.base_salary, row.allowance) == (200000, 68422)
    assert (row.unit_price, row.sales, row.cost_total, row.gross_profit) == (
        800000,
        589474,
        322107,
        267367,
    )
    assert (row.work_status_summary, row.hr_target, row.check_result) == (
        "待機（減額あり）→開発稼働",
        "対象",
        "OK",
    )


def test_unit_price_is_dash_for_month_without_billable_days(
    connection: sqlite3.Connection,
) -> None:
    row = list_monthly_results(connection, "2026-10")[0]

    assert (row.unit_price, row.sales) == (None, 0)


def test_last_billable_unit_price_for_unknown_employee(connection: sqlite3.Connection) -> None:
    assert last_billable_unit_price(connection, 999, _MONTH) is None


def test_payroll_items_in_display_order(connection: sqlite3.Connection) -> None:
    items = list_payroll_items(connection, 1, _MONTH)

    assert [(item.pay_item_name, item.paid_amount, item.deduction_amount) for item in items] == [
        ("基本給", 200000, 0),
        ("勤務手当（職務給）", 50000, 0),
        ("固定残業手当", 18422, 11578),
        ("住宅手当", 0, 10000),
        ("リーダー手当", 0, 10000),
    ]


def test_update_hr_settings_logs_only_changes(connection: sqlite3.Connection) -> None:
    update_hr_settings(connection, 1, _MONTH, "対象外", " 別途精算 ")
    update_hr_settings(connection, 1, _MONTH, "対象外", "別途精算")

    row = list_monthly_results(connection, _MONTH)[0]
    logs = connection.execute(
        "SELECT target_key, before_value, after_value FROM operation_log "
        "WHERE target_table = 'monthly_payroll' AND operation = '更新'"
    ).fetchall()
    assert (row.hr_target, row.hr_note) == ("対象外", "別途精算")
    assert len(logs) == 1
    assert logs[0][0] == f"1:{_MONTH}"
    assert json.loads(logs[0][1]) == {"hr_target": "対象", "hr_note": None}
    assert json.loads(logs[0][2]) == {"hr_target": "対象外", "hr_note": "別途精算"}


def test_update_hr_settings_rejects_closed_month(connection: sqlite3.Connection) -> None:
    close_month(connection, _MONTH)

    with pytest.raises(ClosedMonthError):
        update_hr_settings(connection, 1, _MONTH, "対象外", None)


def test_update_hr_settings_rejects_invalid_target(connection: sqlite3.Connection) -> None:
    with pytest.raises(InvalidHrTargetError):
        update_hr_settings(connection, 1, _MONTH, "未定義", None)


def test_update_hr_settings_rejects_missing_result(connection: sqlite3.Connection) -> None:
    with pytest.raises(ResultNotFoundError):
        update_hr_settings(connection, 1, "2026-12", "対象外", None)


def test_unit_price_of_retire_month_uses_the_last_present_day(
    connection: sqlite3.Connection,
) -> None:
    # 11/20 退職：11/23 以降の営業日は在籍していないため、11/20 の単価を使う。
    connection.execute("UPDATE employee SET retire_date = '2026-11-20' WHERE id = 1")
    connection.commit()

    assert last_billable_unit_price(connection, 1, _MONTH) == 800000


def test_result_without_pl_row_shows_none(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM monthly_pl WHERE year_month = ?", (_MONTH,))
    connection.commit()

    row = list_monthly_results(connection, _MONTH)[0]

    assert (row.sales, row.cost_total, row.gross_profit) == (None, None, None)
