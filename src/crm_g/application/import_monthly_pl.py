"""`IMP-05` 月次売上原価の取込（移行設計書 5.5 `MIG-044`、設計判断 `MGD-005`）。

取込用 CSV の列の定義・名寄せ・行ごとの検証・ファイル内の重複・DB との照合と、
`monthly_pl` への一括投入を行う。

- 対象は20期（`2026-04`〜`2027-03`）だけで、範囲外の年月は `E018`
- 原価合計・粗利は、取込処理が計算する。`source` は `取込`、`is_closed` は 1（確定値）
- DB に同じ社員・年月の行があるとき、再計算で作られた締め前の行（`source` が `再計算`、
  `is_closed` が 0）は置き換え、それ以外の行は `E019` にする
- 取り込んだ行は締め済みのため、取込の後に再計算しない（`MIG-054`）。再計算も、
  取込の行を上書きしない（計算仕様書 8章）
- エラーには、セルの値を含めない（`MIG-005`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from crm_g.application.assignment import ASSIGNMENT_CATEGORIES
from crm_g.application.import_common import (
    ColumnLayout,
    ImportErrorCode,
    ImportIssue,
    ImportRow,
    read_import_csv,
)
from crm_g.application.import_matching import EMAIL_COLUMN, NAME_COLUMN, EmployeeMatch
from crm_g.application.import_rows import (
    EMPLOYEE_KEY_REQUIRED_ONE_OF,
    RowReader,
    amount_or_zero,
    choice,
    key_issues,
    match_rows,
    required_amount,
    required_year_month,
    sorted_issues,
)
from crm_g.application.recalculation import year_month_text
from crm_g.domain.period import fiscal_year_date_range
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now

YEAR_MONTH_COLUMN = "年月"
CATEGORY_COLUMN = "区分"
SALES_COLUMN = "売上"
COST_SALARY_COLUMN = "月給部分の原価"
COST_ALLOWANCE_COLUMN = "月給の外の手当"
COST_BONUS_COLUMN = "賞与"

MONTHLY_PL_LAYOUT = ColumnLayout(
    defined=(
        NAME_COLUMN,
        EMAIL_COLUMN,
        YEAR_MONTH_COLUMN,
        CATEGORY_COLUMN,
        SALES_COLUMN,
        COST_SALARY_COLUMN,
        COST_ALLOWANCE_COLUMN,
        COST_BONUS_COLUMN,
    ),
    required=(YEAR_MONTH_COLUMN, SALES_COLUMN, COST_SALARY_COLUMN),
    required_one_of=EMPLOYEE_KEY_REQUIRED_ONE_OF,
)
"""`IMP-05` の列の定義（`MIG-044`）。"""

TARGET_FISCAL_YEAR = 2026
"""取込の対象の年度（20期。`MIG-044`）。"""

_TARGET_FIRST, _TARGET_LAST = fiscal_year_date_range(TARGET_FISCAL_YEAR)
TARGET_FIRST_MONTH = year_month_text(_TARGET_FIRST.year, _TARGET_FIRST.month)
"""取込の対象の最初の年月（`2026-04`）。"""
TARGET_LAST_MONTH = year_month_text(_TARGET_LAST.year, _TARGET_LAST.month)
"""取込の対象の最後の年月（`2027-03`）。"""

_SOURCE_IMPORTED = "取込"
_SOURCE_RECALCULATED = "再計算"
_CATEGORY_CHOICES = {label: label for label in ASSIGNMENT_CATEGORIES}


@dataclass(frozen=True)
class MonthlyPlRecord:
    """取り込む社員・月の売上原価1件（検証を通った値）。"""

    line_number: int
    employee_id: int
    year_month: str
    category: str | None
    sales: int
    cost_salary: int
    cost_allowance: int
    cost_bonus: int

    @property
    def cost_total(self) -> int:
        """原価合計（月給部分・月給の外の手当・賞与の和）。"""
        return self.cost_salary + self.cost_allowance + self.cost_bonus

    @property
    def gross_profit(self) -> int:
        """粗利（売上 − 原価合計）。負の値を取りうる。"""
        return self.sales - self.cost_total


@dataclass(frozen=True)
class MonthlyPlValidation:
    """月次売上原価の取込用 CSV の検証の結果。`issues` が空のときだけ、`records` を投入できる。

    `row_count` は、空行を除いた読み込んだ行数（`MIG-061`）。`matches` は名寄せの結果。
    """

    row_count: int
    excluded_columns: tuple[str, ...]
    issues: tuple[ImportIssue, ...]
    matches: tuple[EmployeeMatch, ...]
    records: tuple[MonthlyPlRecord, ...]


def _parse_row(
    row: ImportRow, match: EmployeeMatch
) -> tuple[MonthlyPlRecord | None, str | None, list[ImportIssue]]:
    """1行を解釈・検証する。取り込む行と、重複の検査に使う年月（解釈できたとき）を返す。"""
    reader = RowReader(row)
    if match.issue is not None:
        reader.issues.append(match.issue)
    year_month = reader.read(YEAR_MONTH_COLUMN, required_year_month)
    if year_month is not None and not TARGET_FIRST_MONTH <= year_month <= TARGET_LAST_MONTH:
        reader.add(YEAR_MONTH_COLUMN, ImportErrorCode.OUT_OF_TARGET_PERIOD)
    category = reader.read(CATEGORY_COLUMN, choice(_CATEGORY_CHOICES))
    sales = reader.read(SALES_COLUMN, required_amount)
    cost_salary = reader.read(COST_SALARY_COLUMN, required_amount)
    cost_allowance = reader.read(COST_ALLOWANCE_COLUMN, amount_or_zero)
    cost_bonus = reader.read(COST_BONUS_COLUMN, amount_or_zero)

    # 必須の値は、エラーがなければ必ずある（None の判定は、型を絞るため）。
    if (
        reader.issues
        or match.employee_id is None
        or year_month is None
        or sales is None
        or cost_salary is None
        or cost_allowance is None
        or cost_bonus is None
    ):
        return None, year_month, reader.issues
    record = MonthlyPlRecord(
        line_number=row.line_number,
        employee_id=match.employee_id,
        year_month=year_month,
        category=category,
        sales=sales,
        cost_salary=cost_salary,
        cost_allowance=cost_allowance,
        cost_bonus=cost_bonus,
    )
    return record, year_month, reader.issues


def _existing_rows(connection: sqlite3.Connection) -> Mapping[tuple[int, str], tuple[str, int]]:
    """DB の `monthly_pl` の、社員・年月から、`source`・`is_closed` への対応を返す。"""
    rows = connection.execute(
        "SELECT employee_id, year_month, source, is_closed FROM monthly_pl "
        "WHERE year_month BETWEEN ? AND ?",
        (TARGET_FIRST_MONTH, TARGET_LAST_MONTH),
    ).fetchall()
    return {(row[0], row[1]): (row[2], row[3]) for row in rows}


def _is_replaceable(source: str, is_closed: int) -> bool:
    """再計算で作られた締め前の行（置き換えてよい行。`MGD-005`）か。"""
    return source == _SOURCE_RECALCULATED and is_closed == 0


def validate_monthly_pl_csv(connection: sqlite3.Connection, content: bytes) -> MonthlyPlValidation:
    """月次売上原価の取込用 CSV を読み込み、検証する（`MIG-044`、`MIG-050`）。DB は変更しない。

    名寄せ・行ごとの検証 → ファイル内の重複（`E012`）→ DB との照合（`E019`）の順に、
    すべてのエラーを集める。エラーは、行番号の順に並べる（ファイル全体のエラーが先）。
    """
    imported = read_import_csv(content, MONTHLY_PL_LAYOUT)
    matches = match_rows(connection, imported.rows)
    issues: list[ImportIssue] = list(imported.issues)
    records: list[MonthlyPlRecord] = []
    keys: list[tuple[int, tuple[int, str]]] = []
    for row, match in zip(imported.rows, matches, strict=True):
        record, year_month, row_issues = _parse_row(row, match)
        issues.extend(row_issues)
        if record is not None:
            records.append(record)
        if match.employee_id is not None and year_month is not None:
            keys.append((row.line_number, (match.employee_id, year_month)))

    if keys:
        existing = _existing_rows(connection)

        def db_issue(key: tuple[int, str]) -> ImportErrorCode | None:
            found = existing.get(key)
            if found is None or _is_replaceable(*found):
                return None
            return ImportErrorCode.ALREADY_CLOSED

        issues.extend(key_issues(keys, YEAR_MONTH_COLUMN, db_issue))

    sorted_result = sorted_issues(issues)
    return MonthlyPlValidation(
        row_count=len(imported.rows),
        excluded_columns=imported.excluded_columns,
        issues=sorted_result,
        matches=matches,
        records=tuple(records) if not sorted_result else (),
    )


class MonthlyPlConflictError(RuntimeError):
    """投入の時点で、置き換えられない行があったとき（検証の後に DB が変わったとき）の例外。

    メッセージには、値（社員・年月）を含めない（`MIG-005`）。
    """


def insert_monthly_pl(connection: sqlite3.Connection, records: Sequence[MonthlyPlRecord]) -> int:
    """検証を通った月次売上原価を、`monthly_pl` に一括で投入する。投入した件数を返す。

    同じ社員・年月の、再計算で作られた締め前の行は置き換える（作成日時は残す）。
    それ以外の既存の行があれば、何も変えずに `MonthlyPlConflictError` を送出する
    （呼び出し側がロールバックする）。トランザクション・操作ログは、呼び出し側が受け持つ。
    """
    now = to_iso8601(utc_now())
    cursor = connection.executemany(
        "INSERT INTO monthly_pl (employee_id, year_month, category, sales, cost_salary, "
        "cost_allowance, cost_bonus, cost_total, gross_profit, source, is_closed, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
        "ON CONFLICT (employee_id, year_month) DO UPDATE SET "
        "category = excluded.category, sales = excluded.sales, "
        "cost_salary = excluded.cost_salary, cost_allowance = excluded.cost_allowance, "
        "cost_bonus = excluded.cost_bonus, cost_total = excluded.cost_total, "
        "gross_profit = excluded.gross_profit, source = excluded.source, "
        "is_closed = excluded.is_closed, updated_at = excluded.updated_at "
        "WHERE monthly_pl.source = ? AND monthly_pl.is_closed = 0",
        [
            (
                record.employee_id,
                record.year_month,
                record.category,
                record.sales,
                record.cost_salary,
                record.cost_allowance,
                record.cost_bonus,
                record.cost_total,
                record.gross_profit,
                _SOURCE_IMPORTED,
                now,
                now,
                _SOURCE_RECALCULATED,
            )
            for record in records
        ],
    )
    if cursor.rowcount != len(records):
        raise MonthlyPlConflictError("締め済み、または取込済みの月次売上原価があります")
    return len(records)
