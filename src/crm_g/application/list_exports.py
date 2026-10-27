"""各一覧の CSV（画面設計書 9.1、`SCN-080`、`REQ-FUN-012`）。

- 列は、対応する画面の一覧の列と同じにする。値は `UID-090` の形式（金額は整数、日付は
  `YYYY/MM/DD`、率は単位を除いた数値で見出しに単位、値なしは空欄）にする
- 利用者が入力した文字列の列は、数式インジェクションの対策をかける（`UID-091`）
- 給与・賞与を含む一覧（昇給一覧、月次給与計算の一覧、賞与の一覧）は、CSV を作ってから
  対象テーブルごとに `出力` を記録する（`SCD-021`）。売上の集計は、暫定で記録しない（14章-5）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Sequence
from datetime import date

from crm_g.application.bonus_entry import load_bonus_screen
from crm_g.application.csv_export import (
    CsvValue,
    ExportFile,
    build_csv,
    export_file_name,
    record_export,
)
from crm_g.application.employee import list_employees
from crm_g.application.hr_export import NoPayrollResultError
from crm_g.application.monthly_payroll import list_monthly_results
from crm_g.application.salary_history import list_raises
from crm_g.application.sales import LABEL_COLUMNS, display_labels, summarize_sales
from crm_g.application.work_period import list_month_grid
from crm_g.domain.period import half_months
from crm_g.domain.rounding import percent_one_decimal, rate_four_decimals

EMPLOYEE_LIST = "社員一覧"
RAISE_LIST = "昇給一覧"
PAYROLL_LIST = "月次給与計算の一覧"
BONUS_LIST = "賞与の一覧"
WORK_LIST = "稼働の一覧"
SALES_SUMMARY = "売上の集計"


def _date(value: date | None) -> str | None:
    return value.strftime("%Y/%m/%d") if value is not None else None


def _export(
    connection: sqlite3.Connection,
    kind: str,
    condition: str,
    csv_content: bytes,
    logged_tables: Sequence[str] = (),
) -> ExportFile:
    if logged_tables:
        record_export(connection, logged_tables, f"{kind} {condition}")
    return ExportFile(export_file_name(kind, condition), csv_content)


def export_employee_list(
    connection: sqlite3.Connection, enrollment_statuses: Sequence[str]
) -> ExportFile:
    """社員一覧（`SCN-020`）。条件は在籍ステータス。給与・賞与を含まないため記録しない。"""
    rows: list[list[CsvValue]] = [
        [
            employee.name,
            employee.email,
            employee.employee_no,
            employee.employment_type,
            employee.job_type,
            employee.enrollment_status,
            _date(employee.join_date),
            _date(employee.retire_date),
        ]
        for employee in list_employees(connection, enrollment_statuses)
    ]
    content = build_csv(
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
        rows,
        text_columns=(0, 1, 2, 4),
    )
    return _export(connection, EMPLOYEE_LIST, "・".join(enrollment_statuses), content)


def export_raise_list(
    connection: sqlite3.Connection, period_start: date, period_end: date, condition: str
) -> ExportFile:
    """昇給一覧（`SCN-031`）。`condition` は期間の名称（ファイル名・記録に使う）。"""
    rows: list[list[CsvValue]] = [
        [
            row.employee_name,
            _date(row.effective_from),
            row.revision_type,
            row.pay_system,
            row.previous_monthly_salary,
            row.monthly_salary,
            row.raise_amount,
        ]
        for row in list_raises(connection, period_start, period_end)
    ]
    content = build_csv(
        [
            "氏名",
            "適用開始日",
            "改定区分",
            "給与体系",
            "直前の月給合計",
            "新しい月給合計",
            "昇給額",
        ],
        rows,
        text_columns=(0,),
    )
    return _export(
        connection, RAISE_LIST, condition, content, ("salary_history", "salary_history_item")
    )


def export_payroll_list(connection: sqlite3.Connection, year_month: str) -> ExportFile:
    """月次給与計算の一覧（`SCN-032` の結果の一覧）。計算結果がない月はエラー。"""
    results = list_monthly_results(connection, year_month)
    if not results:
        raise NoPayrollResultError(f"{year_month.replace('-', '/')} の給与の計算結果がありません")
    rows: list[list[CsvValue]] = [
        [
            row.employee_name,
            row.monthly_salary,
            row.base_salary,
            row.allowance,
            row.paid_amount,
            row.deduction,
            row.unit_price,
            row.sales,
            row.cost_total,
            row.gross_profit,
            row.work_status_summary,
            row.hr_target,
            row.check_result,
        ]
        for row in results
    ]
    content = build_csv(
        [
            "氏名",
            "月給（円）",
            "基本給（円）",
            "手当（円）",
            "支払額（円）",
            "減額（円）",
            "単価（円）",
            "売上（円）",
            "原価（円）",
            "粗利（円）",
            "稼働状態の要約",
            "人事連携",
            "判定",
        ],
        rows,
        text_columns=(0, 10),
    )
    return _export(
        connection, PAYROLL_LIST, year_month, content, ("monthly_payroll", "monthly_payroll_item")
    )


def export_bonus_list(connection: sqlite3.Connection, eval_half: str) -> ExportFile:
    """賞与の一覧（`SCN-040` の対象者の一覧）。要再計算の賞与額は空欄にする。"""
    rows: list[list[CsvValue]] = [
        [
            row.employee_name,
            row.base_type,
            row.base_amount,
            row.eval_rank,
            rate_four_decimals(row.rate_bp) if row.rate_bp is not None else None,
            None if row.needs_recalculation else row.bonus_amount,
            row.evaluator,
            row.comment,
            "○" if row.is_closed else "×",
        ]
        for row in load_bonus_screen(connection, eval_half).rows
    ]
    content = build_csv(
        [
            "氏名",
            "基準額の種類",
            "基準額（円）",
            "評価ランク",
            "掛け率(倍)",
            "賞与額（円）",
            "評価者",
            "コメント",
            "確定",
        ],
        rows,
        text_columns=(0, 6, 7),
    )
    return _export(connection, BONUS_LIST, eval_half, content, ("bonus",))


def export_work_list(
    connection: sqlite3.Connection,
    fiscal_year: int,
    half: int,
    enrollment_statuses: Sequence[str],
) -> ExportFile:
    """稼働の一覧（`SCN-050` の社員×月の表）。給与・賞与を含まないため記録しない。"""
    months = half_months(fiscal_year, half)
    rows: list[list[CsvValue]] = [
        [row.employee_name, *row.cells]
        for row in list_month_grid(connection, fiscal_year, half, enrollment_statuses)
    ]
    content = build_csv(
        ["氏名", *(f"{year}年{month}月" for year, month in months)],
        rows,
        text_columns=range(len(months) + 1),
    )
    return _export(connection, WORK_LIST, f"{fiscal_year}-H{half}", content)


def export_sales_summary(
    connection: sqlite3.Connection,
    unit: str,
    axis: str,
    fiscal_year: int | None,
    today: date,
) -> ExportFile:
    """売上の集計（`SCN-051` の実績の集計）。暫定で `出力` を記録しない（画面設計書 14章-5）。"""
    summary = summarize_sales(connection, unit, axis, fiscal_year, today)
    label_column = LABEL_COLUMNS.get(axis)
    rows: list[list[CsvValue]] = []
    for row, label in zip(summary.rows, display_labels(summary.rows), strict=True):
        ratio = row.total.gross_profit_ratio
        rows.append(
            [
                row.period,
                *([label] if label_column is not None else []),
                row.total.sales,
                row.total.cost_total,
                row.total.gross_profit,
                percent_one_decimal(ratio) if ratio is not None else None,
            ]
        )
    headers = [
        "期間",
        *([label_column] if label_column else []),
        "売上（円）",
        "原価（円）",
        "粗利（円）",
    ]
    content = build_csv(
        [*headers, "粗利率(%)"], rows, text_columns=(1,) if label_column == "社員" else ()
    )
    condition = "_".join(
        part for part in (unit, str(fiscal_year) if fiscal_year else "全期", axis) if part
    )
    return _export(connection, SALES_SUMMARY, condition, content)
