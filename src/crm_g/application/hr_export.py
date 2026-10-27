"""人事連携用の CSV（画面設計書 9.2、`SCN-080`、要件定義書 7章 9）。

- 行は、対象月の `monthly_payroll` の全行。対象外の社員も「対象ステータス」の列で区別して出す
- 給与項目の内訳は、`pay_item` の全項目を表示順に、支給額・減額の2列ずつ並べる。対象月の
  内訳がない項目は 0 を出す
- 行の並びは、氏名（カナがあればカナ）の昇順
- CSV を作ってから、`monthly_payroll`・`monthly_payroll_item` ごとに `出力` を記録する（`SCD-021`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections import defaultdict

from crm_g.application.csv_export import (
    CsvValue,
    ExportFile,
    build_csv,
    export_file_name,
    record_export,
)
from crm_g.application.recalculation import is_month_closed

HR_EXPORT_KIND = "人事連携"
_LOGGED_TABLES = ("monthly_payroll", "monthly_payroll_item")
# 利用者が入力した文字列の列（氏名、稼働ステータス、備考）。数式インジェクションの対策の対象。
_NAME_COLUMN = 0
_SUMMARY_COLUMN = 2


class NoPayrollResultError(RuntimeError):
    """対象月の給与の計算結果がないときのエラー。"""


def is_unclosed_month_with_results(connection: sqlite3.Connection, year_month: str) -> bool:
    """対象月に給与の計算結果があり、締め前か（画面の「締め前の月（未確定の値）」の表示）。"""
    has_results = connection.execute(
        "SELECT 1 FROM monthly_payroll WHERE year_month = ? LIMIT 1", (year_month,)
    ).fetchone()
    return has_results is not None and not is_month_closed(connection, year_month)


def build_hr_export(connection: sqlite3.Connection, year_month: str) -> ExportFile:
    """対象月（`YYYY-MM`）の人事連携用の CSV を作り、`出力` を記録して返す。"""
    pay_items = connection.execute(
        "SELECT code, name FROM pay_item ORDER BY sort_order, code"
    ).fetchall()
    payrolls = connection.execute(
        "SELECT p.employee_id, e.name, p.hr_target, p.work_status_summary, p.monthly_salary, "
        "p.paid_amount, p.deduction, p.check_result, p.hr_note "
        "FROM monthly_payroll p JOIN employee e ON e.id = p.employee_id "
        "WHERE p.year_month = ? ORDER BY COALESCE(e.name_kana, e.name), e.id",
        (year_month,),
    ).fetchall()
    if not payrolls:
        raise NoPayrollResultError(f"{year_month.replace('-', '/')} の給与の計算結果がありません")
    items: dict[int, dict[str, tuple[int, int]]] = defaultdict(dict)
    for employee_id, code, paid, deduction in connection.execute(
        "SELECT employee_id, pay_item_code, paid_amount, deduction_amount "
        "FROM monthly_payroll_item WHERE year_month = ?",
        (year_month,),
    ).fetchall():
        items[employee_id][code] = (paid, deduction)

    headers = ["氏名", "対象ステータス", "稼働ステータス", "月給", "支払額", "差額（減額）"]
    for _, name in pay_items:
        headers += [f"{name} 支給額", f"{name} 減額"]
    headers += ["正誤判定", "備考"]
    rows: list[list[CsvValue]] = []
    for employee_id, name, target, summary, salary, paid, deduction, check, note in payrolls:
        row: list[CsvValue] = [name, target, summary, salary, paid, deduction]
        for code, _ in pay_items:
            row += list(items[employee_id].get(code, (0, 0)))
        rows.append([*row, check, note])

    content = build_csv(
        headers, rows, text_columns=(_NAME_COLUMN, _SUMMARY_COLUMN, len(headers) - 1)
    )
    record_export(connection, _LOGGED_TABLES, f"{HR_EXPORT_KIND} {year_month}")
    return ExportFile(export_file_name(HR_EXPORT_KIND, year_month), content)
