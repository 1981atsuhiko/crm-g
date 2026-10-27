"""人事連携用の CSV の試験（画面設計書 9.2）。

`TC-FUN-019`、`TC-SCN-018`、`TC-SCN-020`、`TC-SCD-014`。
"""

import csv
import io
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from crm_g.application.hr_export import (
    NoPayrollResultError,
    build_hr_export,
    is_unclosed_month_with_results,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-11-30T00:00:00.000000Z"
_PAY_ITEMS = (
    "基本給",
    "勤務手当（職務給）",
    "固定残業手当",
    "住宅手当",
    "役職手当",
    "一時手当（毎月）",
    "リーダー手当",
    "資格手当",
    "その他手当",
)


def _payroll(
    connection: sqlite3.Connection,
    employee_id: int,
    values: tuple[int, int, int, str, str, str | None],
) -> None:
    """`values` は (月給, 支払額, 減額, 稼働ステータス, 対象ステータス, 備考)。"""
    salary, paid, deduction, summary, target, note = values
    connection.execute(
        "INSERT INTO monthly_payroll (employee_id, year_month, monthly_salary, paid_amount, "
        "deduction, work_status_summary, hr_target, hr_note, check_result, calculated_at, "
        "created_at, updated_at) VALUES (?, '2026-11', ?, ?, ?, ?, ?, ?, 'OK', ?, ?, ?)",
        (
            employee_id,
            salary,
            paid,
            deduction,
            summary,
            target,
            note,
            _TIMESTAMP,
            _TIMESTAMP,
            _TIMESTAMP,
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """社員1：氏名が `=` で始まる（カナ「イ」）。社員2：対象外（カナ「ア」）。"""
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        for employee_id, name, kana in ((1, "=HYPERLINK(1)", "イ"), (2, "青木", "ア")):
            connection.execute(
                "INSERT INTO employee (id, email, name, name_kana, enrollment_status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, '在籍', ?, ?)",
                (employee_id, f"e{employee_id}@example.com", name, kana, _TIMESTAMP, _TIMESTAMP),
            )
        _payroll(connection, 1, (330000, 320000, 10000, "-開発稼働", "対象", "+要確認"))
        _payroll(connection, 2, (250000, 250000, 0, "待機（減額無し）", "対象外", None))
        connection.executemany(
            "INSERT INTO monthly_payroll_item (employee_id, year_month, pay_item_code, "
            "paid_amount, deduction_amount, created_at, updated_at) "
            "VALUES (?, '2026-11', ?, ?, ?, ?, ?)",
            [
                (1, "BASE_SALARY", 300000, 0, _TIMESTAMP, _TIMESTAMP),
                (1, "DUTY_ALLOWANCE", 20000, 10000, _TIMESTAMP, _TIMESTAMP),
                (2, "BASE_SALARY", 250000, 0, _TIMESTAMP, _TIMESTAMP),
            ],
        )
        connection.commit()
        yield connection


def _read(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))


def test_tc_fun_019_columns_and_rows(connection: sqlite3.Connection) -> None:
    export_file = build_hr_export(connection, "2026-11")

    rows = _read(export_file.content)
    item_headers = [f"{name} {kind}" for name in _PAY_ITEMS for kind in ("支給額", "減額")]
    assert rows[0] == [
        "氏名",
        "対象ステータス",
        "稼働ステータス",
        "月給",
        "支払額",
        "差額（減額）",
        *item_headers,
        "正誤判定",
        "備考",
    ]
    zeros = ["0", "0"] * (len(_PAY_ITEMS) - 1)
    # 氏名（カナ）の順。対象外の社員も出す。
    assert rows[1] == ["青木", "対象外", "待機（減額無し）", "250000", "250000", "0"] + [
        "250000",
        "0",
        *zeros,
        "OK",
        "",
    ]
    # 数式として解釈されうる氏名・備考に、単一引用符が付く（TC-SCN-018）。
    assert rows[2][:6] == ["'=HYPERLINK(1)", "対象", "'-開発稼働", "330000", "320000", "10000"]
    assert rows[2][6:10] == ["300000", "0", "20000", "10000"]
    assert rows[2][-2:] == ["OK", "'+要確認"]
    assert export_file.file_name == "人事連携_2026-11.csv"


def test_tc_scn_020_export_is_logged_per_table(connection: sqlite3.Connection) -> None:
    build_hr_export(connection, "2026-11")

    assert connection.execute(
        "SELECT operation, target_table, detail FROM operation_log ORDER BY id"
    ).fetchall() == [
        ("出力", "monthly_payroll", "人事連携 2026-11"),
        ("出力", "monthly_payroll_item", "人事連携 2026-11"),
    ]


def test_unclosed_month_with_results(connection: sqlite3.Connection) -> None:
    assert is_unclosed_month_with_results(connection, "2026-11")
    assert not is_unclosed_month_with_results(connection, "2026-12")
    connection.execute(
        "UPDATE monthly_payroll SET is_closed = 1, closed_at = ? WHERE year_month = '2026-11'",
        (_TIMESTAMP,),
    )
    connection.commit()

    assert not is_unclosed_month_with_results(connection, "2026-11")


def test_pay_items_follow_sort_order_and_names_are_neutralized(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("UPDATE pay_item SET sort_order = 0, name = '=X' WHERE code = 'OTHER'")
    connection.commit()

    headers = _read(build_hr_export(connection, "2026-11").content)[0]

    assert headers[6:10] == ["'=X 支給額", "'=X 減額", "基本給 支給額", "基本給 減額"]


def test_month_without_results_is_an_error_and_not_logged(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(NoPayrollResultError, match="2026/12 の給与の計算結果がありません"):
        build_hr_export(connection, "2026-12")

    assert connection.execute("SELECT COUNT(*) FROM operation_log").fetchone() == (0,)
