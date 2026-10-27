"""ダッシュボードの集計の試験（`SCN-010`、`CLC-024`、`CLC-025`）。"""

import sqlite3
from collections.abc import Iterator
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest

from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.dashboard import PlTotal, WaitingRow, load_dashboard
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"


def _employee(
    connection: sqlite3.Connection,
    employee_id: int,
    name: str,
    *,
    join_date: str = "2020-04-01",
    retire_date: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO employee (id, email, name, enrollment_status, join_date, retire_date, "
        "created_at, updated_at) VALUES (?, ?, ?, '在籍', ?, ?, ?, ?)",
        (
            employee_id,
            f"e{employee_id}@example.com",
            name,
            join_date,
            retire_date,
            _TIMESTAMP,
            _TIMESTAMP,
        ),
    )


def _pl(
    connection: sqlite3.Connection, employee_id: int, year_month: str, sales: int, cost: int
) -> None:
    connection.execute(
        "INSERT INTO monthly_pl (employee_id, year_month, sales, cost_salary, cost_allowance, "
        "cost_bonus, cost_total, gross_profit, source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, 0, ?, ?, 'アプリ計算', ?, ?)",
        (employee_id, year_month, sales, cost, cost, sales - cost, _TIMESTAMP, _TIMESTAMP),
    )


def _period(
    connection: sqlite3.Connection, employee_id: int, start: str, end: str | None, code: str
) -> None:
    connection.execute(
        "INSERT INTO work_period (employee_id, start_date, end_date, work_status_code, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (employee_id, start, end, code, _TIMESTAMP, _TIMESTAMP),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        _employee(connection, 1, "A")
        _employee(connection, 2, "B")
        connection.commit()
        create_year_calendar(connection, 2026)
        yield connection


def test_tc_fun_001_month_totals(connection: sqlite3.Connection) -> None:
    _pl(connection, 1, "2026-10", 600000, 300000)
    _pl(connection, 2, "2026-10", 400000, 500000)

    dashboard = load_dashboard(connection, "2026-10")

    assert dashboard.month_total == PlTotal(1000000, 800000, 200000)
    assert dashboard.month_total.gross_profit_ratio == Fraction(1, 5)
    # 2026-10 は下期の開始月。半期・期は 2026-10 の値だけを入れると、同じ値になる。
    assert dashboard.half_total == dashboard.month_total
    assert dashboard.fiscal_year_total == dashboard.month_total


def test_half_and_fiscal_year_are_cumulative_up_to_the_target_month(
    connection: sqlite3.Connection,
) -> None:
    _pl(connection, 1, "2026-03", 1, 1)  # 前の期
    _pl(connection, 1, "2026-04", 100, 50)
    _pl(connection, 1, "2026-09", 200, 100)
    _pl(connection, 1, "2026-10", 300, 400)
    _pl(connection, 1, "2026-11", 9999, 0)  # 対象月より後

    dashboard = load_dashboard(connection, "2026-10")

    assert dashboard.month_total == PlTotal(300, 400, -100)
    assert dashboard.half_total == PlTotal(300, 400, -100)
    assert dashboard.fiscal_year_total == PlTotal(600, 550, 50)
    assert load_dashboard(connection, "2026-09").half_total == PlTotal(300, 150, 150)


def test_fiscal_year_of_january_to_march_starts_in_the_previous_april(
    connection: sqlite3.Connection,
) -> None:
    _pl(connection, 1, "2025-04", 100, 0)
    _pl(connection, 1, "2025-10", 200, 0)
    _pl(connection, 1, "2026-02", 400, 0)

    dashboard = load_dashboard(connection, "2026-02")

    assert dashboard.half_total == PlTotal(600, 0, 600)
    assert dashboard.fiscal_year_total == PlTotal(700, 0, 700)


def test_tc_fun_002_month_without_results_is_not_calculated(
    connection: sqlite3.Connection,
) -> None:
    dashboard = load_dashboard(connection, "2026-10")

    assert (dashboard.month_total, dashboard.half_total, dashboard.fiscal_year_total) == (
        None,
        None,
        None,
    )


def test_zero_sales_has_no_gross_profit_ratio() -> None:
    assert PlTotal(0, 100, -100).gross_profit_ratio is None


def test_tc_fun_003_man_day_ratio_and_tc_fun_004_waiting(
    connection: sqlite3.Connection,
) -> None:
    _employee(connection, 3, "C")
    _period(connection, 1, "2026-04-01", None, "DEV")
    _period(connection, 2, "2026-09-01", "2026-10-15", "WAIT_FULL")
    _period(connection, 2, "2026-10-16", None, "WAIT_CUT")
    _period(connection, 3, "2026-10-01", None, "EDU")

    dashboard = load_dashboard(connection, "2026-10")

    # A（開発）と C（教育）は売上計上が ○、B（待機）は ×。人日は 3 人とも 21 日。
    assert dashboard.has_calendar
    assert dashboard.man_day_ratio == Fraction(2, 3)
    assert dashboard.waiting == (
        WaitingRow(
            2,
            "B",
            "待機（減額無し）→待機（減額あり）",
            21,
            (
                (date(2026, 10, 1), date(2026, 10, 15)),
                (date(2026, 10, 16), date(2026, 10, 31)),
            ),
        ),
    )


def test_man_day_ratio_counts_only_present_business_days(
    connection: sqlite3.Connection,
) -> None:
    _employee(connection, 3, "C", join_date="2026-10-16")
    _employee(connection, 4, "D", retire_date="2026-09-30")
    for employee_id in (1, 3, 4):
        _period(connection, employee_id, "2026-04-01", None, "DEV")
    _period(connection, 2, "2026-10-01", None, "WAIT_FULL")

    dashboard = load_dashboard(connection, "2026-10")

    # A 21 日・B 21 日（待機）・C 11 日（10/16 入社）。D は退職済みで含めない。
    assert dashboard.man_day_ratio == Fraction(32, 53)


def test_month_without_calendar(connection: sqlite3.Connection) -> None:
    _period(connection, 2, "2027-01-01", None, "WAIT_FULL")

    dashboard = load_dashboard(connection, "2027-01")

    assert not dashboard.has_calendar
    assert dashboard.man_day_ratio is None
    assert dashboard.waiting == ()
