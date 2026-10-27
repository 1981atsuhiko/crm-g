"""各一覧の CSV の試験（画面設計書 9.1、`TC-FUN-020`、`TC-SCN-018`、`TC-SCN-020`）。"""

import csv
import io
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.bonus_entry import EvaluationInput, save_bonus_calculation, save_evaluations
from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.hr_export import NoPayrollResultError
from crm_g.application.list_exports import (
    export_bonus_list,
    export_employee_list,
    export_payroll_list,
    export_raise_list,
    export_sales_summary,
    export_work_list,
)
from crm_g.application.recalculation import recalculate_month
from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.application.work_period import WorkPeriodInput, create_work_period
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"
_STATUSES = ("在籍", "退職予定")


def _salary(connection: sqlite3.Connection, effective_from: date, revision: str, base: int) -> None:
    register_salary_history(
        connection,
        1,
        SalaryHistoryInput(
            effective_from=effective_from,
            pay_system="通常評価制度",
            employment_type="正社員",
            grade=None,
            annual_salary=None,
            revision_type=revision,
            note=None,
            items=(SalaryItemInput("BASE_SALARY", base),),
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """社員1：氏名 `-P`（数式の対策の対象）。4月から待機、10月に昇給。2026-10 を計算済み。"""
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        connection.execute(
            "INSERT INTO employee (id, email, name, employee_no, enrollment_status, join_date, "
            "created_at, updated_at) VALUES (1, 'p@example.com', '-P', 'E001', '在籍', "
            "'2020-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO bonus_rate (eval_half, eval_rank, rate_bp, created_at, updated_at) "
            "VALUES ('2026-H1', 'A', 12500, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
        create_year_calendar(connection, 2026)
        _salary(connection, date(2026, 4, 1), "定期（4月）", 300000)
        _salary(connection, date(2026, 10, 1), "定期（10月）", 310000)
        create_work_period(
            connection,
            1,
            WorkPeriodInput(date(2026, 4, 1), None, "WAIT_FULL", None, "100", None),
        )
        recalculate_month(connection, "2026-10")
        connection.commit()
        yield connection


def _read(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))


def _logs(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    return connection.execute(
        "SELECT target_table, detail FROM operation_log WHERE operation = '出力' ORDER BY id"
    ).fetchall()


def test_employee_list(connection: sqlite3.Connection) -> None:
    export_file = export_employee_list(connection, _STATUSES)

    assert export_file.file_name == "社員一覧_在籍・退職予定.csv"
    assert _read(export_file.content) == [
        [
            "氏名",
            "メールアドレス",
            "社員番号",
            "雇用形態",
            "職種",
            "在籍ステータス",
            "入社日",
            "退職日",
        ],
        ["'-P", "p@example.com", "E001", "", "", "在籍", "2020/04/01", ""],
    ]
    # 給与・賞与を含まないため、記録しない。
    assert _logs(connection) == []


def test_raise_list_is_logged(connection: sqlite3.Connection) -> None:
    export_file = export_raise_list(connection, date(2026, 10, 1), date(2027, 3, 31), "20期下期")

    assert export_file.file_name == "昇給一覧_20期下期.csv"
    assert _read(export_file.content)[1] == [
        "'-P",
        "2026/10/01",
        "定期（10月）",
        "通常評価制度",
        "300000",
        "310000",
        "10000",
    ]
    assert _logs(connection) == [
        ("salary_history", "昇給一覧 20期下期"),
        ("salary_history_item", "昇給一覧 20期下期"),
    ]


def test_payroll_list_is_logged(connection: sqlite3.Connection) -> None:
    export_file = export_payroll_list(connection, "2026-10")

    rows = _read(export_file.content)
    assert export_file.file_name == "月次給与計算の一覧_2026-10.csv"
    assert rows[0][:2] == ["氏名", "月給（円）"]
    assert rows[1][0] == "'-P"
    assert rows[1][1] == "310000"
    # 単価がない（待機）ときは空欄。
    assert rows[1][6] == ""
    assert _logs(connection) == [
        ("monthly_payroll", "月次給与計算の一覧 2026-10"),
        ("monthly_payroll_item", "月次給与計算の一覧 2026-10"),
    ]


def test_payroll_list_without_results(connection: sqlite3.Connection) -> None:
    with pytest.raises(NoPayrollResultError):
        export_payroll_list(connection, "2026-11")

    assert _logs(connection) == []


def test_bonus_list(connection: sqlite3.Connection) -> None:
    save_evaluations(connection, "2026-H1", [EvaluationInput(1, "A", "=上長", None)])
    save_bonus_calculation(connection, "2026-H1")

    export_file = export_bonus_list(connection, "2026-H1")

    assert export_file.file_name == "賞与の一覧_2026-H1.csv"
    assert _read(export_file.content)[1] == [
        "'-P",
        "月給合計",
        "300000",
        "A",
        "1.2500",
        "375000",
        "'=上長",
        "",
        "×",
    ]
    assert _logs(connection) == [("bonus", "賞与の一覧 2026-H1")]


def test_bonus_list_leaves_stale_amount_blank(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO bonus_rate (eval_half, eval_rank, rate_bp, created_at, updated_at) "
        "VALUES ('2026-H1', 'B', 10000, ?, ?)",
        (_TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()
    save_evaluations(connection, "2026-H1", [EvaluationInput(1, "A", None, None)])
    save_bonus_calculation(connection, "2026-H1")
    save_evaluations(connection, "2026-H1", [EvaluationInput(1, "B", None, None)])

    row = _read(export_bonus_list(connection, "2026-H1").content)[1]

    assert (row[3], row[5]) == ("B", "")


def test_work_list(connection: sqlite3.Connection) -> None:
    export_file = export_work_list(connection, 2026, 2, _STATUSES)

    rows = _read(export_file.content)
    assert export_file.file_name == "稼働の一覧_2026-H2.csv"
    assert rows[0] == ["氏名", "2026年10月", "2026年11月", "2026年12月", "2027年1月"] + [
        "2027年2月",
        "2027年3月",
    ]
    assert rows[1][:2] == ["'-P", "待機（減額無し）"]
    assert _logs(connection) == []


def test_sales_summary(connection: sqlite3.Connection) -> None:
    export_file = export_sales_summary(connection, "月別", "合計", 2026, date(2026, 10, 27))

    rows = _read(export_file.content)
    assert export_file.file_name == "売上の集計_月別_2026_合計.csv"
    assert rows[0] == ["期間", "売上（円）", "原価（円）", "粗利（円）", "粗利率(%)"]
    # 売上が 0 のときは、粗利率を空欄にする。
    assert rows[1][0] == "2026/10"
    assert rows[1][1] == "0"
    assert rows[1][4] == ""
    assert _logs(connection) == []


def test_sales_summary_by_employee(connection: sqlite3.Connection) -> None:
    connection.execute("UPDATE monthly_pl SET sales = 1000, gross_profit = 1000 - cost_total")
    connection.commit()

    export_file = export_sales_summary(connection, "期別", "社員別", None, date(2026, 10, 27))

    rows = _read(export_file.content)
    assert export_file.file_name == "売上の集計_期別_全期_社員別.csv"
    assert rows[0] == ["期間", "社員", "売上（円）", "原価（円）", "粗利（円）", "粗利率(%)"]
    assert rows[1][:3] == ["20期", "'-P", "1000"]
    assert rows[1][5].startswith("-")
