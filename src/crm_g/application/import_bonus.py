"""`IMP-06` 賞与の取込（移行設計書 5.6 `MIG-045`）。

取込用 CSV の列の定義・名寄せ・行ごとの検証・ファイル内の重複・DB との照合と、
`bonus` への一括投入を行う。

- 支給月は、査定対象の半期と対応すること（上期は同年10月、下期は翌年4月。`CLC-019`）。
  違反は `E016`
- `source` は `取込`、`is_closed` は 1（支給済みの実績）。評価ランク・掛け率は `NULL`
  （`DBD-020`）。賞与額と基準額×掛け率の一致、対象者の条件は検査しない
- 主キー（社員、査定対象の半期）が、ファイル内で重複すると `E012`、DB に既にあると `E013`
- 賞与は支給月の原価に反映するため、取込の後に、支給月の同じ社員の月を再計算する
  （`MIG-054`。再計算の呼び出しは `data_import` が受け持つ）
- エラーには、セルの値を含めない（`MIG-005`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Sequence
from dataclasses import dataclass

from crm_g.application.bonus import pay_month_of
from crm_g.application.bonus_entry import BASE_TYPES
from crm_g.application.import_common import (
    ColumnLayout,
    ImportErrorCode,
    ImportIssue,
    ImportRow,
    read_import_csv,
)
from crm_g.application.import_matching import EMAIL_COLUMN, NAME_COLUMN, EmployeeMatch
from crm_g.application.import_rows import (
    EMPLOYEE_KEY_COLUMN_GROUP,
    RowReader,
    choice,
    key_issues,
    match_rows,
    required_amount,
    required_half,
    required_year_month,
    sorted_issues,
)
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now

EVAL_HALF_COLUMN = "査定対象の半期"
PAY_MONTH_COLUMN = "支給月"
BASE_TYPE_COLUMN = "基準額の種類"
BASE_AMOUNT_COLUMN = "基準額"
BONUS_AMOUNT_COLUMN = "賞与額"

BONUS_LAYOUT = ColumnLayout(
    defined=(
        NAME_COLUMN,
        EMAIL_COLUMN,
        EVAL_HALF_COLUMN,
        PAY_MONTH_COLUMN,
        BASE_TYPE_COLUMN,
        BASE_AMOUNT_COLUMN,
        BONUS_AMOUNT_COLUMN,
    ),
    required=(
        EVAL_HALF_COLUMN,
        PAY_MONTH_COLUMN,
        BASE_TYPE_COLUMN,
        BASE_AMOUNT_COLUMN,
        BONUS_AMOUNT_COLUMN,
    ),
    required_one_of=(EMPLOYEE_KEY_COLUMN_GROUP,),
)
"""`IMP-06` の列の定義（`MIG-045`）。"""

_SOURCE_IMPORTED = "取込"


@dataclass(frozen=True)
class BonusRecord:
    """取り込む賞与1件（検証を通った値）。"""

    line_number: int
    employee_id: int
    eval_half: str
    pay_month: str
    base_type: str
    base_amount: int
    bonus_amount: int


@dataclass(frozen=True)
class BonusValidation:
    """賞与の取込用 CSV の検証の結果。`issues` が空のときだけ、`records` を投入できる。

    `row_count` は、空行を除いた読み込んだ行数（`MIG-061`）。`matches` は名寄せの結果。
    """

    row_count: int
    excluded_columns: tuple[str, ...]
    issues: tuple[ImportIssue, ...]
    matches: tuple[EmployeeMatch, ...]
    records: tuple[BonusRecord, ...]


def _parse_row(
    row: ImportRow, match: EmployeeMatch
) -> tuple[BonusRecord | None, str | None, list[ImportIssue]]:
    """1行を解釈・検証する。取り込む行と、重複の検査に使う半期（解釈できたとき）を返す。"""
    reader = RowReader(row)
    if match.issue is not None:
        reader.issues.append(match.issue)
    eval_half = reader.read(EVAL_HALF_COLUMN, required_half)
    pay_month = reader.read(PAY_MONTH_COLUMN, required_year_month)
    if eval_half is not None and pay_month is not None and pay_month != pay_month_of(eval_half):
        reader.add(PAY_MONTH_COLUMN, ImportErrorCode.INVALID_COMBINATION)
    base_type = reader.read(BASE_TYPE_COLUMN, choice(BASE_TYPES, required=True))
    base_amount = reader.read(BASE_AMOUNT_COLUMN, required_amount)
    bonus_amount = reader.read(BONUS_AMOUNT_COLUMN, required_amount)

    # 必須の値は、エラーがなければ必ずある（None の判定は、型を絞るため）。
    if (
        reader.issues
        or match.employee_id is None
        or eval_half is None
        or pay_month is None
        or base_type is None
        or base_amount is None
        or bonus_amount is None
    ):
        return None, eval_half, reader.issues
    record = BonusRecord(
        line_number=row.line_number,
        employee_id=match.employee_id,
        eval_half=eval_half,
        pay_month=pay_month,
        base_type=base_type,
        base_amount=base_amount,
        bonus_amount=bonus_amount,
    )
    return record, eval_half, reader.issues


def _existing_keys(connection: sqlite3.Connection) -> frozenset[tuple[int, str]]:
    """DB の `bonus` の主キー（社員、査定対象の半期）を返す。"""
    rows = connection.execute("SELECT employee_id, eval_half FROM bonus").fetchall()
    return frozenset((row[0], row[1]) for row in rows)


def validate_bonus_csv(connection: sqlite3.Connection, content: bytes) -> BonusValidation:
    """賞与の取込用 CSV を読み込み、検証する（`MIG-045`、`MIG-050`）。DB は変更しない。

    名寄せ・行ごとの検証 → ファイル内の重複（`E012`）→ DB との照合（`E013`）の順に、
    すべてのエラーを集める。エラーは、行番号の順に並べる（ファイル全体のエラーが先）。
    """
    imported = read_import_csv(content, BONUS_LAYOUT)
    matches = match_rows(connection, imported.rows)
    issues: list[ImportIssue] = list(imported.issues)
    records: list[BonusRecord] = []
    keys: list[tuple[int, tuple[int, str]]] = []
    for row, match in zip(imported.rows, matches, strict=True):
        record, eval_half, row_issues = _parse_row(row, match)
        issues.extend(row_issues)
        if record is not None:
            records.append(record)
        if match.employee_id is not None and eval_half is not None:
            keys.append((row.line_number, (match.employee_id, eval_half)))

    if keys:
        existing = _existing_keys(connection)

        def db_issue(key: tuple[int, str]) -> ImportErrorCode | None:
            return ImportErrorCode.DUPLICATE_KEY_IN_DB if key in existing else None

        issues.extend(key_issues(keys, EVAL_HALF_COLUMN, db_issue))

    sorted_result = sorted_issues(issues)
    return BonusValidation(
        row_count=len(imported.rows),
        excluded_columns=imported.excluded_columns,
        issues=sorted_result,
        matches=matches,
        records=tuple(records) if not sorted_result else (),
    )


def insert_bonuses(connection: sqlite3.Connection, records: Sequence[BonusRecord]) -> int:
    """検証を通った賞与を、`bonus` に一括で追加する。追加した件数を返す。

    トランザクション・操作ログ・再計算は、呼び出し側が受け持つ。
    """
    now = to_iso8601(utc_now())
    connection.executemany(
        "INSERT INTO bonus (employee_id, eval_half, pay_month, base_type, base_amount, "
        "eval_rank, rate_bp, bonus_amount, source, is_closed, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, 1, ?, ?)",
        [
            (
                record.employee_id,
                record.eval_half,
                record.pay_month,
                record.base_type,
                record.base_amount,
                record.bonus_amount,
                _SOURCE_IMPORTED,
                now,
                now,
            )
            for record in records
        ],
    )
    return len(records)


def employees_by_pay_month(
    records: Sequence[BonusRecord],
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """取り込む賞与を、支給月ごとの社員にまとめる（取込の後の再計算の対象。`MIG-054`）。

    支給月の古い順、社員 ID の順に並べる。
    """
    grouped: dict[str, set[int]] = {}
    for record in records:
        grouped.setdefault(record.pay_month, set()).add(record.employee_id)
    return tuple((pay_month, tuple(sorted(grouped[pay_month]))) for pay_month in sorted(grouped))
