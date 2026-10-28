"""`IMP-04` 単価履歴の取込（移行設計書 5.4 `MIG-043`）。

取込用 CSV の1行が、1件の単価の適用開始である。`rate_history` に一括で投入する。

- 単価は、必ず参画に紐づける（`DBD-018`）。顧客名が空の行は `E006`。顧客名なしで
  案件名がある行は `E016`
- 参画は、その社員の DB の参画から、顧客名（案件名があるときは案件名も）が正規化名
  （`MIG-030`）で一致するものを探す。1件に決まらないとき（なし・複数）は `E015`。
  `IMP-03` の取り込みの後に探せる
- 適用開始日が、参画の開始日〜終了日の外にあるときは `E016`
- 一意の組（参画、適用開始日）が、CSV 内で重複すると `E012`、DB に既にあると `E013`
- 投入は、既存の `register_rate_history` を使わない。1行ごとに操作ログ・再計算を行い、
  例外のメッセージに値を含むため。操作ログ・再計算・トランザクションは、呼び出し側
  （`data_import`）が受け持つ
- エラーには、セルの値を含めない（`MIG-005`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

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
    free_text,
    key_issues,
    match_rows,
    required_amount,
    required_date,
    required_free_text,
    sorted_issues,
)
from crm_g.domain.name_normalization import normalized_name
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now

EFFECTIVE_FROM_COLUMN = "適用開始日"
UNIT_PRICE_COLUMN = "単価"
CUSTOMER_NAME_COLUMN = "顧客名"
PROJECT_NAME_COLUMN = "案件名"
REVISION_BASIS_COLUMN = "改定の根拠"
NOTE_COLUMN = "備考"

RATE_HISTORY_LAYOUT = ColumnLayout(
    defined=(
        NAME_COLUMN,
        EMAIL_COLUMN,
        EFFECTIVE_FROM_COLUMN,
        UNIT_PRICE_COLUMN,
        CUSTOMER_NAME_COLUMN,
        PROJECT_NAME_COLUMN,
        REVISION_BASIS_COLUMN,
        NOTE_COLUMN,
    ),
    required=(EFFECTIVE_FROM_COLUMN, UNIT_PRICE_COLUMN, CUSTOMER_NAME_COLUMN),
    required_one_of=EMPLOYEE_KEY_REQUIRED_ONE_OF,
)
"""`IMP-04` の列の定義（`MIG-043`）。"""


@dataclass(frozen=True)
class RateHistoryRecord:
    """取り込む単価履歴1件（検証を通った値）。"""

    line_number: int
    employee_id: int
    assignment_id: int
    effective_from: date
    unit_price: int
    revision_basis: str | None
    note: str | None


@dataclass(frozen=True)
class RateHistoryValidation:
    """単価履歴の取込用 CSV の検証の結果。`issues` が空のときだけ、`records` を投入できる。

    `row_count` は、空行を除いた読み込んだ行数（`MIG-061`）。`matches` は名寄せの結果。
    """

    row_count: int
    excluded_columns: tuple[str, ...]
    issues: tuple[ImportIssue, ...]
    matches: tuple[EmployeeMatch, ...]
    records: tuple[RateHistoryRecord, ...]


@dataclass(frozen=True)
class _Assignment:
    """社員の DB の参画（照合に使う値）。顧客名・案件名は正規化名。"""

    id: int
    customer_key: str
    project_key: str
    start_date: date
    end_date: date | None


@dataclass(frozen=True)
class _ParsedRow:
    """1行の解釈の結果。`record` は、すべての検証を通ったときだけ持つ。

    `assignment_id`・`effective_from` は、ファイル内・DB との重複の検査に使う。
    """

    line_number: int
    assignment_id: int | None
    effective_from: date | None
    record: RateHistoryRecord | None


def _assignments_by_employee(
    connection: sqlite3.Connection, employee_ids: set[int]
) -> dict[int, list[_Assignment]]:
    """社員ごとの、DB の参画。"""
    grouped: dict[int, list[_Assignment]] = {}
    for employee_id in sorted(employee_ids):
        rows = connection.execute(
            "SELECT id, customer_name, project_name, start_date, end_date "
            "FROM assignment WHERE employee_id = ? ORDER BY id",
            (employee_id,),
        ).fetchall()
        grouped[employee_id] = [
            _Assignment(
                id=row[0],
                customer_key=normalized_name(row[1]),
                project_key=normalized_name(row[2]) if row[2] else "",
                start_date=date.fromisoformat(row[3]),
                end_date=date.fromisoformat(row[4]) if row[4] else None,
            )
            for row in rows
        ]
    return grouped


def _find_assignment(
    reader: RowReader,
    candidates: Sequence[_Assignment],
    customer_name: str,
    project_name: str | None,
) -> _Assignment | None:
    """顧客名（案件名があるときは案件名も）が一致する参画を探す。1件に決まらなければ `E015`。"""
    customer_key = normalized_name(customer_name)
    project_key = normalized_name(project_name) if project_name else None
    found = [
        assignment
        for assignment in candidates
        if assignment.customer_key == customer_key
        and (project_key is None or assignment.project_key == project_key)
    ]
    if len(found) != 1:
        reader.add(CUSTOMER_NAME_COLUMN, ImportErrorCode.REFERENCE_NOT_RESOLVED)
        return None
    return found[0]


def _parse_row(
    row: ImportRow, match: EmployeeMatch, assignments: Mapping[int, Sequence[_Assignment]]
) -> tuple[_ParsedRow, list[ImportIssue]]:
    """1行を解釈・検証する（行ごとの検証と、参画の照合）。名寄せのエラーも含める。"""
    reader = RowReader(row)
    if match.issue is not None:
        reader.issues.append(match.issue)
    effective_from = reader.read(EFFECTIVE_FROM_COLUMN, required_date)
    unit_price = reader.read(UNIT_PRICE_COLUMN, required_amount)
    customer_name = reader.read(CUSTOMER_NAME_COLUMN, required_free_text)
    project_name = reader.read(PROJECT_NAME_COLUMN, free_text)
    if customer_name is None and project_name is not None:
        reader.add(PROJECT_NAME_COLUMN, ImportErrorCode.INVALID_COMBINATION)
    revision_basis = reader.read(REVISION_BASIS_COLUMN, free_text)
    note = reader.read(NOTE_COLUMN, free_text)

    assignment: _Assignment | None = None
    if match.employee_id is not None and customer_name is not None:
        assignment = _find_assignment(
            reader, assignments.get(match.employee_id, ()), customer_name, project_name
        )
    if (
        assignment is not None
        and effective_from is not None
        and not _within(effective_from, assignment)
    ):
        reader.add(EFFECTIVE_FROM_COLUMN, ImportErrorCode.INVALID_COMBINATION)

    record: RateHistoryRecord | None = None
    # 必須の列は、エラーがなければ必ず値がある（None の判定は、型を絞るため）。
    if not (
        reader.issues
        or match.employee_id is None
        or assignment is None
        or effective_from is None
        or unit_price is None
    ):
        record = RateHistoryRecord(
            line_number=row.line_number,
            employee_id=match.employee_id,
            assignment_id=assignment.id,
            effective_from=effective_from,
            unit_price=unit_price,
            revision_basis=revision_basis,
            note=note,
        )
    parsed = _ParsedRow(
        line_number=row.line_number,
        assignment_id=None if assignment is None else assignment.id,
        effective_from=effective_from,
        record=record,
    )
    return parsed, reader.issues


def _within(day: date, assignment: _Assignment) -> bool:
    """日付が、参画の開始日〜終了日（空は継続中）の中にあるか（データ設計書 10章 No.12）。"""
    return assignment.start_date <= day and (
        assignment.end_date is None or day <= assignment.end_date
    )


def _duplicate_issues(
    connection: sqlite3.Connection, parsed_rows: Sequence[_ParsedRow]
) -> list[ImportIssue]:
    """（参画、適用開始日）の重複を検査する。DB にあれば `E013`、CSV の2回目以降は `E012`。"""
    keys = [
        (parsed.line_number, (parsed.assignment_id, parsed.effective_from))
        for parsed in parsed_rows
        if parsed.assignment_id is not None and parsed.effective_from is not None
    ]

    def db_issue(key: tuple[int, date]) -> ImportErrorCode | None:
        assignment_id, effective_from = key
        exists = connection.execute(
            "SELECT 1 FROM rate_history WHERE assignment_id = ? AND effective_from = ?",
            (assignment_id, effective_from.isoformat()),
        ).fetchone()
        return None if exists is None else ImportErrorCode.DUPLICATE_KEY_IN_DB

    return key_issues(keys, EFFECTIVE_FROM_COLUMN, db_issue)


def validate_rate_history_csv(
    connection: sqlite3.Connection, content: bytes
) -> RateHistoryValidation:
    """単価履歴の取込用 CSV を読み込み、名寄せ・検証する（`MIG-043`、`MIG-050`）。DB は変更しない。

    行ごとの検証（名寄せ・参画の照合を含む）→ ファイル内・DB の重複の順に、すべてのエラーを集める。
    """
    imported = read_import_csv(content, RATE_HISTORY_LAYOUT)
    matches = match_rows(connection, imported.rows)
    employee_ids = {match.employee_id for match in matches if match.employee_id is not None}
    assignments = _assignments_by_employee(connection, employee_ids)

    issues: list[ImportIssue] = list(imported.issues)
    parsed_rows: list[_ParsedRow] = []
    for row, match in zip(imported.rows, matches, strict=True):
        parsed, row_issues = _parse_row(row, match, assignments)
        parsed_rows.append(parsed)
        issues.extend(row_issues)
    issues.extend(_duplicate_issues(connection, parsed_rows))

    records = tuple(parsed.record for parsed in parsed_rows if parsed.record is not None)
    return RateHistoryValidation(
        row_count=len(imported.rows),
        excluded_columns=imported.excluded_columns,
        issues=sorted_issues(issues),
        matches=matches,
        records=records if not issues else (),
    )


def insert_rate_histories(
    connection: sqlite3.Connection, records: Sequence[RateHistoryRecord]
) -> int:
    """検証を通った単価履歴を、一括で追加する。追加した件数を返す。

    トランザクション・操作ログ・再計算は、呼び出し側が受け持つ。
    """
    now = to_iso8601(utc_now())
    connection.executemany(
        "INSERT INTO rate_history (employee_id, assignment_id, effective_from, unit_price, "
        "revision_basis, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                record.employee_id,
                record.assignment_id,
                record.effective_from.isoformat(),
                record.unit_price,
                record.revision_basis,
                record.note,
                now,
                now,
            )
            for record in records
        ],
    )
    return len(records)
