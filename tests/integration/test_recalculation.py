"""再計算の試験（`ARC-009`、`CLC-022`）。

数値は計算仕様書 9章の EX-001（給与）・EX-005（売上）・EX-006（原価・粗利）による。
"""

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.assignment import AssignmentInput, create_assignment
from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.recalculation import (
    CalculationErrorReason,
    calculated_open_months,
    is_month_closed,
    list_calculation_errors,
    months_between,
    recalculate_month,
    recalculate_range,
    recalculate_since,
    run_manual_recalculation,
)
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
_ITEMS = (
    ("BASE_SALARY", 200000),
    ("DUTY_ALLOWANCE", 50000),
    ("FIXED_OVERTIME", 30000),
    ("HOUSING", 10000),
    ("LEADER", 10000),
)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
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
        yield connection


def _salary(connection: sqlite3.Connection, employee_id: int = 1) -> None:
    register_salary_history(
        connection,
        employee_id,
        SalaryHistoryInput(
            effective_from=date(2026, 4, 1),
            pay_system="通常評価制度",
            employment_type="正社員",
            grade=None,
            annual_salary=None,
            revision_type="定期（4月）",
            note=None,
            items=tuple(SalaryItemInput(code, amount) for code, amount in _ITEMS),
        ),
    )


def _period(
    connection: sqlite3.Connection,
    start: date,
    end: date | None,
    status: str,
    assignment_id: int | None = None,
) -> None:
    create_work_period(
        connection,
        1,
        WorkPeriodInput(
            start_date=start,
            end_date=end,
            work_status_code=status,
            assignment_id=assignment_id,
            billing_ratio_percent="100",
            note=None,
        ),
    )


def _assignment(connection: sqlite3.Connection, unit_price: int | None = 800000) -> int:
    assignment_id = create_assignment(
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
    if unit_price is not None:
        register_rate_history(
            connection,
            assignment_id,
            RateHistoryInput(
                effective_from=date(2026, 11, 1),
                unit_price=unit_price,
                revision_basis=None,
                note=None,
            ),
        )
    return assignment_id


def _ex001(connection: sqlite3.Connection, unit_price: int | None = 800000) -> None:
    """EX-001：11/2〜11/9 は待機（減額あり 60%）、11/10 以降は開発稼働。"""
    _salary(connection)
    assignment_id = _assignment(connection, unit_price)
    _period(connection, date(2026, 11, 1), date(2026, 11, 9), "WAIT_CUT")
    _period(connection, date(2026, 11, 10), None, "DEV", assignment_id)


def _payroll(connection: sqlite3.Connection, year_month: str = _MONTH) -> tuple[object, ...] | None:
    row: tuple[object, ...] | None = connection.execute(
        "SELECT monthly_salary, paid_amount, deduction, check_result, work_status_summary, "
        "hr_target, hr_note FROM monthly_payroll WHERE employee_id = 1 AND year_month = ?",
        (year_month,),
    ).fetchone()
    return row


def _commit_recalculate(connection: sqlite3.Connection, year_month: str = _MONTH) -> object:
    result = recalculate_month(connection, year_month)
    connection.commit()
    return result


def test_tc_fun_011_ex001_payroll_revenue_and_cost(connection: sqlite3.Connection) -> None:
    _ex001(connection)

    result = recalculate_month(connection, _MONTH)
    connection.commit()

    assert (result.year_months, result.saved_count, result.errors) == ((_MONTH,), 1, ())
    assert _payroll(connection) == (
        300000,
        268422,
        31578,
        "OK",
        "待機（減額あり）→開発稼働",
        "対象",
        None,
    )
    items = dict(
        connection.execute(
            "SELECT pay_item_code, paid_amount || '/' || deduction_amount "
            "FROM monthly_payroll_item WHERE employee_id = 1 AND year_month = ?",
            (_MONTH,),
        ).fetchall()
    )
    assert items == {
        "BASE_SALARY": "200000/0",
        "DUTY_ALLOWANCE": "50000/0",
        "FIXED_OVERTIME": "18422/11578",
        "HOUSING": "0/10000",
        "LEADER": "0/10000",
    }
    pl = connection.execute(
        "SELECT category, sales, cost_salary, cost_allowance, cost_bonus, cost_total, "
        "gross_profit, source, is_closed FROM monthly_pl WHERE employee_id = 1"
    ).fetchone()
    assert pl == ("SES", 589474, 322107, 0, 0, 322107, 267367, "再計算", 0)


def test_tc_dbd_013_item_totals_match_payroll(connection: sqlite3.Connection) -> None:
    _ex001(connection)
    _commit_recalculate(connection)

    paid, deduction = connection.execute(
        "SELECT SUM(paid_amount), SUM(deduction_amount) FROM monthly_payroll_item "
        "WHERE employee_id = 1 AND year_month = ?",
        (_MONTH,),
    ).fetchone()
    payroll = _payroll(connection)
    assert payroll is not None
    assert (paid, deduction) == (payroll[1], payroll[2])


def test_tc_scn_012_recalculation_keeps_hr_input(connection: sqlite3.Connection) -> None:
    _ex001(connection)
    _commit_recalculate(connection)
    connection.execute(
        "UPDATE monthly_payroll SET hr_target = '対象外', hr_note = '別途精算' "
        "WHERE employee_id = 1 AND year_month = ?",
        (_MONTH,),
    )
    connection.commit()

    _commit_recalculate(connection)

    payroll = _payroll(connection)
    assert payroll is not None
    assert payroll[5:] == ("対象外", "別途精算")


def _close(connection: sqlite3.Connection, year_month: str = _MONTH) -> None:
    connection.execute(
        "UPDATE monthly_payroll SET is_closed = 1, closed_at = ? WHERE year_month = ?",
        (_TIMESTAMP, year_month),
    )
    connection.execute("UPDATE monthly_pl SET is_closed = 1 WHERE year_month = ?", (year_month,))
    connection.commit()


def test_tc_clc_018_closed_month_is_not_recalculated(connection: sqlite3.Connection) -> None:
    _ex001(connection)
    _commit_recalculate(connection)
    _close(connection)
    before = _payroll(connection)
    connection.execute(
        "UPDATE salary_history_item SET amount = amount + 1000 WHERE pay_item_code = 'BASE_SALARY'"
    )
    connection.commit()

    result = recalculate_month(connection, _MONTH)

    assert is_month_closed(connection, _MONTH)
    assert (result.year_months, result.saved_count) == ((), 0)
    assert _payroll(connection) == before


def test_imported_pl_row_is_not_overwritten(connection: sqlite3.Connection) -> None:
    _ex001(connection)
    connection.execute(
        "INSERT INTO monthly_pl (employee_id, year_month, category, sales, cost_salary, "
        "cost_allowance, cost_bonus, cost_total, gross_profit, source, is_closed, "
        "created_at, updated_at) VALUES (1, ?, 'SES', 1, 1, 0, 0, 1, 0, '取込', 0, ?, ?)",
        (_MONTH, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()

    _commit_recalculate(connection)

    sales, source = connection.execute("SELECT sales, source FROM monthly_pl").fetchone()
    assert (sales, source) == (1, "取込")


def test_closed_bonus_of_the_month_is_added_to_cost(connection: sqlite3.Connection) -> None:
    _ex001(connection)
    connection.execute(
        "INSERT INTO bonus (employee_id, eval_half, pay_month, base_type, base_amount, "
        "eval_rank, rate_bp, bonus_amount, source, is_closed, created_at, updated_at) "
        "VALUES (1, '2026-H1', '2026-10', '月給合計', 300000, 'A', 12500, 375000, "
        "'アプリ計算', 1, ?, ?)",
        (_TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()
    _salary_and_dev_for_october(connection)

    _commit_recalculate(connection, "2026-10")

    cost_bonus, cost_total = connection.execute(
        "SELECT cost_bonus, cost_total FROM monthly_pl WHERE year_month = '2026-10'"
    ).fetchone()
    assert cost_bonus == 375000
    assert cost_total == 375000 + 360000


def _salary_and_dev_for_october(connection: sqlite3.Connection) -> None:
    # 10月は全営業日を、売上計上でない待機（減額なし、100%）とする。原価は 300,000 × 1.2。
    _period(connection, date(2026, 10, 1), date(2026, 10, 31), "WAIT_FULL")


def test_zero_unit_price_is_valid_and_gives_zero_sales(connection: sqlite3.Connection) -> None:
    _ex001(connection, unit_price=0)

    result = recalculate_month(connection, _MONTH)

    assert result.errors == ()
    assert connection.execute("SELECT sales FROM monthly_pl").fetchone() == (0,)


def test_tc_cal_004_missing_unit_price_is_an_error_and_keeps_previous_result(
    connection: sqlite3.Connection,
) -> None:
    _ex001(connection)
    _commit_recalculate(connection)
    before = _payroll(connection)
    connection.execute("DELETE FROM rate_history")
    connection.commit()

    result = recalculate_month(connection, _MONTH)

    assert [(error.employee_id, error.reason) for error in result.errors] == [
        (1, CalculationErrorReason.MISSING_UNIT_PRICE)
    ]
    assert result.saved_count == 0
    assert _payroll(connection) == before


def test_tc_cal_003_overlapping_periods_are_an_error(connection: sqlite3.Connection) -> None:
    _salary(connection)
    _period(connection, date(2026, 11, 1), None, "WAIT_FULL")
    # 重なり禁止に反する異常なデータを、直接入れる。
    connection.execute(
        "INSERT INTO work_period (employee_id, start_date, work_status_code, billing_ratio_bp, "
        "created_at, updated_at) VALUES (1, '2026-11-15', 'TRN_FULL', 10000, ?, ?)",
        (_TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()

    result = recalculate_month(connection, _MONTH)

    assert [error.reason for error in result.errors] == [CalculationErrorReason.OVERLAPPING_PERIODS]
    assert _payroll(connection) is None


def test_tc_clc_016_month_without_business_days_is_an_error(
    connection: sqlite3.Connection,
) -> None:
    _salary(connection)

    result = recalculate_month(connection, "2027-01")

    assert [error.reason for error in result.errors] == [CalculationErrorReason.NO_BUSINESS_DAY]


def test_missing_salary_history_is_an_error(connection: sqlite3.Connection) -> None:
    _period(connection, date(2026, 11, 1), None, "WAIT_FULL")

    result = recalculate_month(connection, _MONTH)

    assert [error.reason for error in result.errors] == [CalculationErrorReason.NO_SALARY_HISTORY]


def test_employee_without_present_business_day_is_an_error(
    connection: sqlite3.Connection,
) -> None:
    # 在籍期間に営業日がない例：11/28（土）入社・11/29（日）退職。
    connection.execute(
        "UPDATE employee SET join_date = '2026-11-28', retire_date = '2026-11-29' WHERE id = 1"
    )
    connection.commit()
    _salary(connection)

    result = recalculate_month(connection, _MONTH)

    assert [error.reason for error in result.errors] == [CalculationErrorReason.NOT_PRESENT]


def test_employees_outside_the_month_are_not_targets(connection: sqlite3.Connection) -> None:
    connection.execute("UPDATE employee SET join_date = '2026-12-01' WHERE id = 1")
    connection.commit()

    result = recalculate_month(connection, _MONTH)

    assert (result.saved_count, result.errors) == (0, ())


def test_missing_cost_coefficient_is_an_error(connection: sqlite3.Connection) -> None:
    _ex001(connection)
    connection.execute("DELETE FROM system_setting WHERE setting_key = '原価係数'")
    connection.commit()

    result = recalculate_month(connection, _MONTH)

    assert [error.reason for error in result.errors] == [CalculationErrorReason.NO_COST_COEFFICIENT]


def test_list_calculation_errors_does_not_save(connection: sqlite3.Connection) -> None:
    _ex001(connection, unit_price=None)

    errors = list_calculation_errors(connection, _MONTH)

    assert [(error.employee_name, error.reason) for error in errors] == [
        ("山田太郎", CalculationErrorReason.MISSING_UNIT_PRICE)
    ]
    assert _payroll(connection) is None


def test_recalculate_since_targets_calculated_open_months_only(
    connection: sqlite3.Connection,
) -> None:
    _ex001(connection)
    _salary_and_dev_for_october(connection)
    _commit_recalculate(connection, "2026-10")
    _commit_recalculate(connection, _MONTH)
    _close(connection, "2026-10")

    assert calculated_open_months(connection, date(2026, 4, 1)) == (_MONTH,)

    connection.execute(
        "UPDATE salary_history_item SET amount = 210000 WHERE pay_item_code = 'BASE_SALARY'"
    )
    result = recalculate_since(connection, date(2026, 10, 15), employee_ids=[1])
    connection.commit()

    assert result.year_months == (_MONTH,)
    payroll = _payroll(connection)
    assert payroll is not None
    assert payroll[0] == 310000


def test_tc_arc_016_since_and_manual_recalculation_give_the_same_result(
    connection: sqlite3.Connection,
) -> None:
    _ex001(connection)
    _commit_recalculate(connection)
    by_update = (_payroll(connection), connection.execute("SELECT * FROM monthly_pl").fetchone())

    recalculate_since(connection, date(2026, 11, 1), employee_ids=[1])
    connection.commit()
    by_since = (_payroll(connection), connection.execute("SELECT * FROM monthly_pl").fetchone())

    assert by_update[0] == by_since[0]
    assert by_update[1][:9] == by_since[1][:9]


def test_recalculate_since_with_other_employee_does_not_touch_this_one(
    connection: sqlite3.Connection,
) -> None:
    _ex001(connection)
    _commit_recalculate(connection)
    before = connection.execute("SELECT calculated_at FROM monthly_payroll").fetchone()

    result = recalculate_since(connection, date(2026, 11, 1), employee_ids=[999])

    assert result.saved_count == 0
    assert connection.execute("SELECT calculated_at FROM monthly_payroll").fetchone() == before


def test_list_calculation_errors_is_empty_when_calculation_succeeds(
    connection: sqlite3.Connection,
) -> None:
    _ex001(connection)

    assert list_calculation_errors(connection, _MONTH) == ()


def test_manual_recalculation_commits_by_itself(connection: sqlite3.Connection) -> None:
    _ex001(connection)

    result = run_manual_recalculation(connection, _MONTH)
    connection.rollback()

    assert result.saved_count == 1
    assert _payroll(connection) is not None


def _paid_and_deduction(connection: sqlite3.Connection, employee_id: int = 1) -> tuple[int, int]:
    row = connection.execute(
        "SELECT paid_amount, deduction FROM monthly_payroll "
        "WHERE employee_id = ? AND year_month = ?",
        (employee_id, _MONTH),
    ).fetchone()
    return int(row[0]), int(row[1])


def test_ex002_join_month_is_prorated(connection: sqlite3.Connection) -> None:
    """EX-002：11/16 入社。在籍営業日 10 日で日割りする（待機（減額無し）100% で確かめる）。"""
    connection.execute("UPDATE employee SET join_date = '2026-11-16' WHERE id = 1")
    _salary(connection)
    _period(connection, date(2026, 11, 16), None, "WAIT_FULL")

    _commit_recalculate(connection)

    assert _paid_and_deduction(connection) == (157895, 142105)
    base = connection.execute(
        "SELECT paid_amount, deduction_amount FROM monthly_payroll_item "
        "WHERE pay_item_code = 'BASE_SALARY'"
    ).fetchone()
    assert base == (107895, 92105)


def test_retire_month_is_prorated(connection: sqlite3.Connection) -> None:
    # 11/13 退職：在籍営業日は 11/2、4、5、6、9、10、11、12、13 の 9 日。300,000 × 9 ÷ 19。
    connection.execute("UPDATE employee SET retire_date = '2026-11-13' WHERE id = 1")
    _salary(connection)
    _period(connection, date(2026, 11, 1), None, "WAIT_FULL")

    _commit_recalculate(connection)

    assert _paid_and_deduction(connection) == (142106, 157894)


def test_salary_history_of_the_basis_date_is_used_for_the_whole_month(
    connection: sqlite3.Connection,
) -> None:
    _salary(connection)
    register_salary_history(
        connection,
        1,
        SalaryHistoryInput(
            effective_from=date(2026, 11, 16),
            pay_system="通常評価制度",
            employment_type="正社員",
            grade=None,
            annual_salary=None,
            revision_type="その他",
            note=None,
            items=tuple(
                SalaryItemInput(code, amount + (20000 if code == "BASE_SALARY" else 0))
                for code, amount in _ITEMS
            ),
        ),
    )
    _period(connection, date(2026, 11, 1), None, "WAIT_FULL")

    _commit_recalculate(connection)

    payroll = _payroll(connection)
    assert payroll is not None
    assert payroll[:3] == (320000, 320000, 0)


def test_month_with_only_closed_pl_row_is_treated_as_closed(
    connection: sqlite3.Connection,
) -> None:
    _ex001(connection)
    connection.execute(
        "INSERT INTO monthly_pl (employee_id, year_month, category, sales, cost_salary, "
        "cost_allowance, cost_bonus, cost_total, gross_profit, source, is_closed, "
        "created_at, updated_at) VALUES (1, ?, 'SES', 1, 1, 0, 0, 1, 0, '再計算', 1, ?, ?)",
        (_MONTH, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()

    result = recalculate_month(connection, _MONTH)

    assert result.saved_count == 0
    assert connection.execute("SELECT sales FROM monthly_pl").fetchone() == (1,)


def test_category_is_that_of_the_last_billable_day(connection: sqlite3.Connection) -> None:
    _salary(connection)
    first = _assignment(connection)
    second = create_assignment(
        connection,
        1,
        AssignmentInput(
            customer_name="B社",
            project_name=None,
            category="受託",
            start_date=date(2026, 11, 1),
            end_date=None,
        ),
    )
    register_rate_history(
        connection,
        second,
        RateHistoryInput(
            effective_from=date(2026, 11, 1), unit_price=700000, revision_basis=None, note=None
        ),
    )
    _period(connection, date(2026, 11, 1), date(2026, 11, 15), "DEV", first)
    _period(connection, date(2026, 11, 16), None, "DEV", second)

    _commit_recalculate(connection)

    assert connection.execute("SELECT category FROM monthly_pl").fetchone() == ("受託",)


def test_recalculate_month_with_employee_ids_targets_only_them(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        "INSERT INTO employee "
        "(id, email, name, enrollment_status, join_date, created_at, updated_at) VALUES "
        "(2, 'hanako@example.com', '鈴木花子', '在籍', '2020-04-01', ?, ?)",
        (_TIMESTAMP, _TIMESTAMP),
    )
    _salary(connection)
    _salary(connection, employee_id=2)
    _period(connection, date(2026, 11, 1), None, "WAIT_FULL")

    result = recalculate_month(connection, _MONTH, employee_ids=[1])

    assert (result.saved_count, result.errors) == (1, ())
    assert connection.execute("SELECT employee_id FROM monthly_payroll").fetchall() == [(1,)]


def test_months_between_crosses_the_year_and_is_empty_when_reversed() -> None:
    """取込の後の再計算の月の範囲（`MIG-054`）。年をまたぐ。逆順なら空。"""
    assert months_between(date(2025, 11, 30), date(2026, 2, 1)) == (
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    )
    assert months_between(date(2026, 3, 1), date(2026, 2, 28)) == ()


def test_recalculate_range_includes_months_not_yet_calculated(
    connection: sqlite3.Connection,
) -> None:
    """取込の後の再計算は、未計算の月も計算する（`MIG-054`、ユーザー承認 2026-10-27）。"""
    _salary(connection)
    _period(connection, date(2026, 10, 1), None, "WAIT_FULL")
    assert calculated_open_months(connection, date(2026, 10, 1)) == ()

    result = recalculate_range(connection, date(2026, 10, 15), date(2026, 11, 3), [1])
    connection.commit()

    assert (result.year_months, result.saved_count, result.errors) == (
        ("2026-10", "2026-11"),
        2,
        (),
    )
    assert result.closed_months == ()
    assert _payroll(connection, "2026-10") is not None
    assert _payroll(connection, "2026-11") is not None


def test_recalculate_range_skips_closed_months_and_other_employees(
    connection: sqlite3.Connection,
) -> None:
    """締め済みの月は計算しない。指定した社員だけを計算する。"""
    connection.execute(
        "INSERT INTO employee "
        "(id, email, name, enrollment_status, join_date, created_at, updated_at) VALUES "
        "(2, 'hanako@example.com', '鈴木花子', '在籍', '2020-04-01', ?, ?)",
        (_TIMESTAMP, _TIMESTAMP),
    )
    _salary(connection)
    _salary(connection, employee_id=2)
    _period(connection, date(2026, 10, 1), None, "WAIT_FULL")
    recalculate_month(connection, "2026-10", employee_ids=[1])
    connection.commit()
    _close(connection, "2026-10")

    result = recalculate_range(connection, date(2026, 10, 1), date(2026, 11, 30), [2])
    connection.commit()

    assert (result.year_months, result.closed_months) == (("2026-11",), ("2026-10",))
    rows = connection.execute(
        "SELECT employee_id, year_month FROM monthly_payroll ORDER BY year_month"
    ).fetchall()
    assert rows == [(1, "2026-10"), (2, "2026-11")]
