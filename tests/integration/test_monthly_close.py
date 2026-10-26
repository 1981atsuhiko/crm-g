"""月次締めと解除の試験（`ARC-010`、`TC-ARC-017`、`TC-ARC-018`、`UID-076`）。"""

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.monthly_close import (
    AlreadyClosedError,
    NoResultError,
    NotClosedError,
    UnacknowledgedErrorsError,
    close_month,
    get_month_status,
    reopen_month,
)
from crm_g.application.recalculation import recalculate_month, recalculate_since
from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.application.work_period import WorkPeriodInput, create_work_period
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-25T00:00:00.000000Z"
_MONTH = "2026-11"


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) VALUES "
            "(1, 'taro@example.com', '山田太郎', '在籍', '2020-04-01', ?, ?), "
            "(2, 'hanako@example.com', '鈴木花子', '在籍', '2020-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP, _TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
        create_year_calendar(connection, 2026)
        for employee_id in (1, 2):
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
                    items=(SalaryItemInput("BASE_SALARY", 300000),),
                ),
            )
            create_work_period(
                connection,
                employee_id,
                WorkPeriodInput(
                    start_date=date(2026, 4, 1),
                    end_date=None,
                    work_status_code="WAIT_FULL",
                    assignment_id=None,
                    billing_ratio_percent="100",
                    note=None,
                ),
            )
        recalculate_month(connection, _MONTH)
        connection.commit()
        yield connection


def _flags(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return connection.execute(
        "SELECT p.is_closed, p.closed_at IS NOT NULL, l.is_closed FROM monthly_payroll p "
        "JOIN monthly_pl l USING (employee_id, year_month) WHERE p.year_month = ? "
        "ORDER BY p.employee_id",
        (_MONTH,),
    ).fetchall()


def _logs(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return connection.execute(
        "SELECT operation, target_table, detail FROM operation_log "
        "WHERE operation IN ('月次締め', '月次締め解除') ORDER BY id"
    ).fetchall()


def test_tc_arc_017_close_sets_flags_and_stops_recalculation(
    connection: sqlite3.Connection,
) -> None:
    close_month(connection, _MONTH)

    assert _flags(connection) == [(1, 1, 1), (1, 1, 1)]
    status = get_month_status(connection, _MONTH)
    assert status.is_closed
    assert status.closed_at is not None
    assert _logs(connection) == [
        ("月次締め", "monthly_payroll", _MONTH),
        ("月次締め", "monthly_pl", _MONTH),
    ]

    connection.execute("UPDATE salary_history_item SET amount = 400000")
    result = recalculate_since(connection, date(2026, 4, 1))
    connection.commit()

    assert result.year_months == ()
    assert connection.execute(
        "SELECT DISTINCT monthly_salary FROM monthly_payroll WHERE year_month = ?", (_MONTH,)
    ).fetchall() == [(300000,)]


def test_tc_arc_018_reopen_clears_flags_and_logs(connection: sqlite3.Connection) -> None:
    close_month(connection, _MONTH)

    reopen_month(connection, _MONTH)

    assert _flags(connection) == [(0, 0, 0), (0, 0, 0)]
    assert connection.execute(
        "SELECT COUNT(*) FROM monthly_payroll WHERE closed_at IS NOT NULL"
    ).fetchone() == (0,)
    assert not get_month_status(connection, _MONTH).is_closed
    assert _logs(connection)[-2:] == [
        ("月次締め解除", "monthly_payroll", _MONTH),
        ("月次締め解除", "monthly_pl", _MONTH),
    ]
    assert recalculate_month(connection, _MONTH).year_months == (_MONTH,)


def test_reopen_keeps_imported_pl_rows_closed(connection: sqlite3.Connection) -> None:
    # 移行で取り込んだ確定値は、常に締め済み（移行設計書 MGD-005）。
    connection.execute("UPDATE monthly_pl SET source = '取込', is_closed = 1 WHERE employee_id = 2")
    connection.commit()
    close_month(connection, _MONTH)

    reopen_month(connection, _MONTH)

    assert connection.execute(
        "SELECT employee_id, is_closed FROM monthly_pl ORDER BY employee_id"
    ).fetchall() == [(1, 0), (2, 1)]
    # 取込の行があっても、解除した月は締め前に戻り、再計算できる（移行設計書 MIG-054）。
    assert not get_month_status(connection, _MONTH).is_closed
    assert recalculate_month(connection, _MONTH).year_months == (_MONTH,)


def test_month_with_imported_pl_rows_only_is_open_and_recalculable(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        "INSERT INTO monthly_pl (employee_id, year_month, category, sales, cost_salary, "
        "cost_allowance, cost_bonus, cost_total, gross_profit, source, is_closed, "
        "created_at, updated_at) VALUES (1, '2026-10', 'SES', 1, 1, 0, 0, 1, 0, '取込', 1, ?, ?)",
        (_TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()

    assert not get_month_status(connection, "2026-10").is_closed
    result = recalculate_month(connection, "2026-10")
    connection.commit()

    assert result.saved_count == 2
    assert connection.execute(
        "SELECT sales, source, is_closed FROM monthly_pl "
        "WHERE employee_id = 1 AND year_month = '2026-10'"
    ).fetchone() == (1, "取込", 1)

    close_month(connection, "2026-10")
    reopen_month(connection, "2026-10")

    assert not get_month_status(connection, "2026-10").is_closed


def test_tc_scn_011_month_with_errors_needs_acknowledgement(
    connection: sqlite3.Connection,
) -> None:
    # 社員2の稼働期間に、重なる期間を直接入れて、計算エラーにする。
    connection.execute(
        "INSERT INTO work_period (employee_id, start_date, work_status_code, billing_ratio_bp, "
        "created_at, updated_at) VALUES (2, '2026-11-15', 'TRN_FULL', 10000, ?, ?)",
        (_TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()

    with pytest.raises(UnacknowledgedErrorsError):
        close_month(connection, _MONTH)
    assert not get_month_status(connection, _MONTH).is_closed
    assert _logs(connection) == []

    close_month(connection, _MONTH, acknowledge_errors=True)

    assert get_month_status(connection, _MONTH).is_closed


def test_close_twice_is_rejected(connection: sqlite3.Connection) -> None:
    close_month(connection, _MONTH)

    with pytest.raises(AlreadyClosedError):
        close_month(connection, _MONTH)


def test_month_without_results_cannot_be_closed(connection: sqlite3.Connection) -> None:
    with pytest.raises(NoResultError):
        close_month(connection, "2026-12")


def test_reopen_of_open_month_is_rejected(connection: sqlite3.Connection) -> None:
    with pytest.raises(NotClosedError):
        reopen_month(connection, _MONTH)


def test_status_of_open_month(connection: sqlite3.Connection) -> None:
    status = get_month_status(connection, _MONTH)

    assert (status.is_closed, status.closed_at) == (False, None)
