"""売上の実績の集計の試験（`SCN-051`、`TC-FUN-012`、`CLC-024`）。"""

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.dashboard import PlTotal
from crm_g.application.sales import (
    InvalidSalesConditionError,
    SalesRow,
    last_fiscal_year,
    summarize_sales,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"
_TODAY = date(2026, 7, 15)


def _pl(
    connection: sqlite3.Connection,
    key: tuple[int, str, str | None],
    amounts: tuple[int, int],
    *,
    is_closed: int = 0,
) -> None:
    """`key` は (社員ID, 年月, カテゴリ)、`amounts` は (売上, 原価)。"""
    employee_id, year_month, category = key
    sales, cost = amounts
    connection.execute(
        "INSERT INTO monthly_pl (employee_id, year_month, category, sales, cost_salary, "
        "cost_allowance, cost_bonus, cost_total, gross_profit, source, is_closed, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, 'アプリ計算', ?, ?, ?)",
        (
            employee_id,
            year_month,
            category,
            sales,
            cost,
            cost,
            sales - cost,
            is_closed,
            _TIMESTAMP,
            _TIMESTAMP,
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """社員1：カナ「イ」、社員2：カナ「ア」（社員別は、カナの順で 2 → 1）。"""
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        for employee_id, name, kana in ((1, "井上", "イノウエ"), (2, "青木", "アオキ")):
            connection.execute(
                "INSERT INTO employee (id, email, name, name_kana, enrollment_status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, '在籍', ?, ?)",
                (employee_id, f"e{employee_id}@example.com", name, kana, _TIMESTAMP, _TIMESTAMP),
            )
        _pl(connection, (1, "2025-10", "SES"), (200, 100), is_closed=1)
        _pl(connection, (1, "2026-04", "SES"), (600, 300))
        _pl(connection, (2, "2026-04", None), (400, 500))
        _pl(connection, (1, "2026-05", "受託"), (100, 50), is_closed=1)
        connection.commit()
        yield connection


def test_monthly_total(connection: sqlite3.Connection) -> None:
    summary = summarize_sales(connection, "月別", "合計", 2026, _TODAY)

    assert summary.rows == (
        SalesRow("2026/04", None, PlTotal(1000, 800, 200)),
        SalesRow("2026/05", None, PlTotal(100, 50, 50)),
    )
    # 今日（2026-07-15）の月までで、計算結果がない月。8月以降は数えない。
    assert summary.missing_months == ("2026-06", "2026-07")
    assert summary.includes_open_months


def test_tc_fun_012_by_category_puts_rows_without_category_into_uncategorized(
    connection: sqlite3.Connection,
) -> None:
    summary = summarize_sales(connection, "月別", "カテゴリ別", 2026, _TODAY)

    assert summary.rows == (
        SalesRow("2026/04", "SES", PlTotal(600, 300, 300)),
        SalesRow("2026/04", "未分類", PlTotal(400, 500, -100)),
        SalesRow("2026/05", "受託", PlTotal(100, 50, 50)),
    )


def test_by_employee_per_half_is_ordered_by_kana(connection: sqlite3.Connection) -> None:
    summary = summarize_sales(connection, "半期別", "社員別", 2026, _TODAY)

    assert summary.rows == (
        SalesRow("20期 上期", "青木", PlTotal(400, 500, -100), employee_id=2),
        SalesRow("20期 上期", "井上", PlTotal(700, 350, 350), employee_id=1),
    )


def test_second_half_and_previous_fiscal_year(connection: sqlite3.Connection) -> None:
    summary = summarize_sales(connection, "半期別", "合計", 2025, _TODAY)

    assert summary.rows == (SalesRow("19期 下期", None, PlTotal(200, 100, 100)),)
    assert not summary.includes_open_months
    assert len(summary.missing_months) == 11


def test_per_fiscal_year_covers_all_fiscal_years(connection: sqlite3.Connection) -> None:
    summary = summarize_sales(connection, "期別", "合計", None, _TODAY)

    assert summary.rows == (
        SalesRow("19期", None, PlTotal(200, 100, 100)),
        SalesRow("20期", None, PlTotal(1100, 850, 250)),
    )
    # 2025-04〜2026-07 の16か月のうち、計算結果がある3か月を除く。
    assert len(summary.missing_months) == 13
    assert summary.missing_months[0] == "2025-04"


def test_per_fiscal_year_includes_older_imported_data(connection: sqlite3.Connection) -> None:
    _pl(connection, (1, "2024-05", "SES"), (50, 0), is_closed=1)

    summary = summarize_sales(connection, "期別", "合計", None, _TODAY)

    assert summary.rows[0] == SalesRow("18期", None, PlTotal(50, 0, 50))
    assert summary.missing_months[0] == "2024-04"


def test_without_results(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")

        summary = summarize_sales(connection, "期別", "合計", None, _TODAY)

    assert summary.rows == ()
    assert summary.missing_months[0] == "2025-04"
    assert not summary.includes_open_months


@pytest.mark.parametrize(
    ("unit", "axis", "fiscal_year"),
    [("週別", "合計", 2026), ("月別", "顧客別", 2026), ("月別", "合計", None)],
)
def test_invalid_condition(
    connection: sqlite3.Connection, unit: str, axis: str, fiscal_year: int | None
) -> None:
    with pytest.raises(InvalidSalesConditionError):
        summarize_sales(connection, unit, axis, fiscal_year, _TODAY)


def test_january_to_march_belong_to_the_previous_fiscal_year(
    connection: sqlite3.Connection,
) -> None:
    _pl(connection, (1, "2027-01", "SES"), (300, 100))

    summary = summarize_sales(connection, "月別", "合計", 2026, date(2027, 2, 10))

    assert [row.period for row in summary.rows] == ["2026/04", "2026/05", "2027/01"]
    # 2027-02 は今日の月なので数え、2027-03 は来ていないので数えない。
    assert summary.missing_months[-2:] == ("2026-12", "2027-02")
    half = summarize_sales(connection, "半期別", "合計", 2026, date(2027, 2, 10))
    assert half.rows[-1] == SalesRow("20期 下期", None, PlTotal(300, 100, 200))


def test_employees_with_the_same_name_are_kept_apart(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO employee (id, email, name, name_kana, enrollment_status, created_at, "
        "updated_at) VALUES (3, 'e3@example.com', '井上', 'イノウエ', '在籍', ?, ?)",
        (_TIMESTAMP, _TIMESTAMP),
    )
    _pl(connection, (3, "2026-04", "SES"), (10, 0))

    summary = summarize_sales(connection, "月別", "社員別", 2026, _TODAY)

    assert [(row.label, row.employee_id) for row in summary.rows[:3]] == [
        ("青木", 2),
        ("井上", 1),
        ("井上", 3),
    ]


def test_next_fiscal_year_is_included_in_march(connection: sqlite3.Connection) -> None:
    _pl(connection, (1, "2027-04", "SES"), (500, 0))
    today = date(2027, 3, 31)

    summary = summarize_sales(connection, "期別", "合計", None, today)

    assert last_fiscal_year(today) == 2027
    # 上期（9月）の次の半期は同じ期の下期、下期（10月）の次は翌期の上期。
    assert last_fiscal_year(date(2026, 9, 30)) == 2026
    assert last_fiscal_year(date(2026, 10, 27)) == 2027
    assert summary.rows[-1] == SalesRow("21期", None, PlTotal(500, 0, 500))
