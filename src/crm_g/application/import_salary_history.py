"""`IMP-02` 給与履歴の取込（移行設計書 5.2 `MIG-041`）。

取込用 CSV の1行が、1件の給与履歴（改定）である。`salary_history` と
`salary_history_item` に一括で投入する。

- 給与項目の列は、見出しを給与項目マスタ（`pay_item`）の名称と照合する。マスタの
  有効期間は検査しない（ユーザー承認済み）。空のセルは、その項目の行を作らない
- 内訳の列がすべて空の行は `E006`。`年俸制` と年俸年収の組み合わせの誤りは `E016`
- 同じ社員・同じ適用開始日の行が CSV 内にあるときは `E012`
- 取込は、履歴の追記だけを受け付ける。DB の最新の履歴より前（以前）の行は `E017`
- 適用終了日は、取込処理が設定する（`MGD-002`）。同じ社員の行の、次の行の適用開始日の
  前日とする。DB の最新の履歴の適用終了日が空なら、新しい最初の行の前日で閉じる
- 投入は、既存の `register_salary_history` を使わない。1行ごとに操作ログ・再計算を行い、
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
    code_text,
    free_text,
    key_issues,
    match_rows,
    optional_amount,
    required_choice,
    required_date,
    sorted_issues,
)
from crm_g.application.salary_history import EMPLOYMENT_TYPES, PAY_SYSTEMS, REVISION_TYPES
from crm_g.domain.salary_revision import determine_previous_effective_to
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now

EFFECTIVE_FROM_COLUMN = "適用開始日"
PAY_SYSTEM_COLUMN = "給与体系"
EMPLOYMENT_TYPE_COLUMN = "雇用形態"
GRADE_COLUMN = "等級"
ANNUAL_SALARY_COLUMN = "年俸年収"
REVISION_TYPE_COLUMN = "改定区分"
NOTE_COLUMN = "備考"
PAY_ITEM_COLUMNS_LABEL = "給与項目の列"
"""内訳の列がすべて空（`E006`）のときの、エラーの列名（移行設計書 5.2 の表の名称）。"""

ANNUAL_PAY_SYSTEM = "年俸制"

_FIXED_COLUMNS = (
    NAME_COLUMN,
    EMAIL_COLUMN,
    EFFECTIVE_FROM_COLUMN,
    PAY_SYSTEM_COLUMN,
    EMPLOYMENT_TYPE_COLUMN,
    GRADE_COLUMN,
    ANNUAL_SALARY_COLUMN,
    REVISION_TYPE_COLUMN,
    NOTE_COLUMN,
)
_REQUIRED_COLUMNS = (
    EFFECTIVE_FROM_COLUMN,
    PAY_SYSTEM_COLUMN,
    EMPLOYMENT_TYPE_COLUMN,
    REVISION_TYPE_COLUMN,
)

_PAY_SYSTEM_CHOICES = {label: label for label in PAY_SYSTEMS}
_EMPLOYMENT_TYPE_CHOICES = {label: label for label in EMPLOYMENT_TYPES}
_REVISION_TYPE_CHOICES = {label: label for label in REVISION_TYPES}


def salary_history_layout(pay_item_names: Sequence[str]) -> ColumnLayout:
    """`IMP-02` の列の定義（`MIG-041`）。給与項目マスタの名称の列を、定義の列に加える。"""
    return ColumnLayout(
        defined=(*_FIXED_COLUMNS, *pay_item_names),
        required=_REQUIRED_COLUMNS,
        required_one_of=EMPLOYEE_KEY_REQUIRED_ONE_OF,
    )


@dataclass(frozen=True)
class SalaryHistoryRecord:
    """取り込む給与履歴1件（検証を通った値）。`items` は（給与項目コード、金額）の並び。"""

    line_number: int
    employee_id: int
    effective_from: date
    pay_system: str
    employment_type: str
    grade: str | None
    annual_salary: int | None
    revision_type: str
    note: str | None
    items: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class SalaryHistoryValidation:
    """給与履歴の取込用 CSV の検証の結果。`issues` が空のときだけ、`records` を投入できる。

    `row_count` は、空行を除いた読み込んだ行数（`MIG-061`）。`matches` は名寄せの結果。
    """

    row_count: int
    excluded_columns: tuple[str, ...]
    issues: tuple[ImportIssue, ...]
    matches: tuple[EmployeeMatch, ...]
    records: tuple[SalaryHistoryRecord, ...]


@dataclass(frozen=True)
class _ParsedRow:
    """1行の解釈の結果。`record` は、名寄せと行ごとの検証を通ったときだけ持つ。

    `employee_id`・`effective_from` は、ファイル内・DB との検証に使う
    （他の列のエラーがあっても持つ）。
    """

    line_number: int
    employee_id: int | None
    effective_from: date | None
    record: SalaryHistoryRecord | None


@dataclass(frozen=True)
class _LatestHistory:
    """社員の、DB の最新の給与履歴（適用開始日が最も遅いもの）。"""

    id: int
    effective_from: date
    effective_to: date | None


def _pay_item_codes_by_name(connection: sqlite3.Connection) -> dict[str, str]:
    """給与項目マスタの名称から、コードへの対応（表示順）。有効期間は問わない。"""
    rows = connection.execute(
        "SELECT name, code FROM pay_item ORDER BY sort_order, code"
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _read_items(
    reader: RowReader, row: ImportRow, codes_by_name: Mapping[str, str]
) -> tuple[tuple[str, int], ...]:
    """給与項目の列を読み、（コード、金額）の並びにする。すべて空なら `E006` を記録する。"""
    items: list[tuple[str, int]] = []
    has_value = False
    for name, code in codes_by_name.items():
        if code_text(row.values.get(name)) is None:
            continue
        has_value = True
        amount = reader.read(name, optional_amount)
        if amount is not None:
            items.append((code, amount))
    if not has_value:
        reader.add(PAY_ITEM_COLUMNS_LABEL, ImportErrorCode.REQUIRED_EMPTY)
    return tuple(items)


def _check_annual_salary(reader: RowReader, row: ImportRow, pay_system: str | None) -> None:
    """給与体系と年俸年収の組み合わせを検査する（`年俸制` のときだけ値を入れる。`E016`）。

    組み合わせは、セルが空かどうかで判定する。
    """
    if pay_system is None:
        return
    has_text = code_text(row.values.get(ANNUAL_SALARY_COLUMN)) is not None
    if pay_system == ANNUAL_PAY_SYSTEM:
        # 値があるのに解釈できないときは、E007 などで報告済み。
        if not has_text:
            reader.add(ANNUAL_SALARY_COLUMN, ImportErrorCode.INVALID_COMBINATION)
    elif has_text:
        reader.add(ANNUAL_SALARY_COLUMN, ImportErrorCode.INVALID_COMBINATION)


def _parse_row(
    row: ImportRow, match: EmployeeMatch, codes_by_name: Mapping[str, str]
) -> tuple[_ParsedRow, list[ImportIssue]]:
    """1行を解釈・検証する（行ごとの検証）。名寄せのエラーも、この行のエラーに含める。"""
    reader = RowReader(row)
    if match.issue is not None:
        reader.issues.append(match.issue)
    effective_from = reader.read(EFFECTIVE_FROM_COLUMN, required_date)
    pay_system = reader.read(PAY_SYSTEM_COLUMN, required_choice(_PAY_SYSTEM_CHOICES))
    employment_type = reader.read(EMPLOYMENT_TYPE_COLUMN, required_choice(_EMPLOYMENT_TYPE_CHOICES))
    grade = reader.read(GRADE_COLUMN, free_text)
    annual_salary = reader.read(ANNUAL_SALARY_COLUMN, optional_amount)
    revision_type = reader.read(REVISION_TYPE_COLUMN, required_choice(_REVISION_TYPE_CHOICES))
    note = reader.read(NOTE_COLUMN, free_text)
    items = _read_items(reader, row, codes_by_name)
    _check_annual_salary(reader, row, pay_system)

    record: SalaryHistoryRecord | None = None
    # 必須の列は、エラーがなければ必ず値がある（None の判定は、型を絞るため）。
    if not (
        reader.issues
        or match.employee_id is None
        or effective_from is None
        or pay_system is None
        or employment_type is None
        or revision_type is None
    ):
        record = SalaryHistoryRecord(
            line_number=row.line_number,
            employee_id=match.employee_id,
            effective_from=effective_from,
            pay_system=pay_system,
            employment_type=employment_type,
            grade=grade,
            annual_salary=annual_salary,
            revision_type=revision_type,
            note=note,
            items=items,
        )
    parsed = _ParsedRow(row.line_number, match.employee_id, effective_from, record)
    return parsed, reader.issues


def _duplicate_issues(parsed_rows: Sequence[_ParsedRow]) -> list[ImportIssue]:
    """同じ社員・同じ適用開始日の行を、2回目以降の行で `E012` にする（ファイル内の検証）。"""
    keys = [
        (parsed.line_number, (parsed.employee_id, parsed.effective_from))
        for parsed in parsed_rows
        if parsed.employee_id is not None and parsed.effective_from is not None
    ]
    # DB との照合は、最新の履歴との比較で別に行う（ここでは、ファイル内の重複だけを見る）。
    return key_issues(keys, EFFECTIVE_FROM_COLUMN, lambda _key: None)


def _latest_histories(
    connection: sqlite3.Connection, employee_ids: set[int]
) -> dict[int, _LatestHistory]:
    """社員ごとの、DB の最新の給与履歴。履歴がない社員は含めない。"""
    latest: dict[int, _LatestHistory] = {}
    for employee_id in sorted(employee_ids):
        row = connection.execute(
            "SELECT id, effective_from, effective_to FROM salary_history "
            "WHERE employee_id = ? ORDER BY effective_from DESC LIMIT 1",
            (employee_id,),
        ).fetchone()
        if row is not None:
            latest[employee_id] = _LatestHistory(
                id=row[0],
                effective_from=date.fromisoformat(row[1]),
                effective_to=date.fromisoformat(row[2]) if row[2] else None,
            )
    return latest


def _history_order_issues(
    parsed_rows: Sequence[_ParsedRow], latest: Mapping[int, _LatestHistory]
) -> list[ImportIssue]:
    """DB の最新の履歴より前（以前）の行を `E017` にする（追記だけを受け付ける。`MGD-002`）。

    最新の履歴の適用終了日があるときは、その日以前の行も `E017` とする。
    """
    issues: list[ImportIssue] = []
    for parsed in parsed_rows:
        if parsed.employee_id is None or parsed.effective_from is None:
            continue
        history = latest.get(parsed.employee_id)
        if history is None:
            continue
        # 適用終了日は、適用開始日以後（TBL-009 の検査）。
        last_covered = history.effective_to or history.effective_from
        if parsed.effective_from <= last_covered:
            issues.append(
                ImportIssue(
                    parsed.line_number,
                    EFFECTIVE_FROM_COLUMN,
                    ImportErrorCode.INVALID_HISTORY_ORDER,
                )
            )
    return issues


def validate_salary_history_csv(
    connection: sqlite3.Connection, content: bytes
) -> SalaryHistoryValidation:
    """給与履歴の取込用 CSV を読み込み、名寄せ・検証する（`MIG-041`、`MIG-050`）。DB は変更しない。

    行ごとの検証（名寄せを含む）→ ファイル内の重複 → DB との照合の順に、すべてのエラーを集める。
    """
    codes_by_name = _pay_item_codes_by_name(connection)
    imported = read_import_csv(content, salary_history_layout(tuple(codes_by_name)))
    matches = match_rows(connection, imported.rows)

    issues: list[ImportIssue] = list(imported.issues)
    parsed_rows: list[_ParsedRow] = []
    for row, match in zip(imported.rows, matches, strict=True):
        parsed, row_issues = _parse_row(row, match, codes_by_name)
        parsed_rows.append(parsed)
        issues.extend(row_issues)

    issues.extend(_duplicate_issues(parsed_rows))
    employee_ids = {parsed.employee_id for parsed in parsed_rows if parsed.employee_id is not None}
    issues.extend(_history_order_issues(parsed_rows, _latest_histories(connection, employee_ids)))

    records = tuple(parsed.record for parsed in parsed_rows if parsed.record is not None)
    return SalaryHistoryValidation(
        row_count=len(imported.rows),
        excluded_columns=imported.excluded_columns,
        issues=sorted_issues(issues),
        matches=matches,
        records=records if not issues else (),
    )


def insert_salary_histories(
    connection: sqlite3.Connection, records: Sequence[SalaryHistoryRecord]
) -> int:
    """検証を通った給与履歴を、一括で追加する。追加した件数を返す（`MGD-002`）。

    社員ごとに適用開始日の順に並べ、各行の適用終了日を、次の行の適用開始日の前日にする。
    DB の最新の履歴の適用終了日が空なら、新しい最初の行の適用開始日の前日を設定する。
    トランザクション・操作ログ・再計算は、呼び出し側が受け持つ。
    """
    now = to_iso8601(utc_now())
    by_employee: dict[int, list[SalaryHistoryRecord]] = {}
    for record in records:
        by_employee.setdefault(record.employee_id, []).append(record)
    latest = _latest_histories(connection, set(by_employee))

    for employee_id, employee_records in by_employee.items():
        ordered = sorted(employee_records, key=lambda record: record.effective_from)
        history = latest.get(employee_id)
        if history is not None and history.effective_to is None:
            closing = determine_previous_effective_to(
                ordered[0].effective_from, history.effective_from
            )
            connection.execute(
                "UPDATE salary_history SET effective_to = ?, updated_at = ? WHERE id = ?",
                (closing.isoformat(), now, history.id),
            )
        for position, record in enumerate(ordered):
            following = ordered[position + 1] if position + 1 < len(ordered) else None
            effective_to = (
                None
                if following is None
                else determine_previous_effective_to(
                    following.effective_from, record.effective_from
                )
            )
            _insert_history(connection, record, effective_to, now)
    return len(records)


def _insert_history(
    connection: sqlite3.Connection,
    record: SalaryHistoryRecord,
    effective_to: date | None,
    now: str,
) -> None:
    """給与履歴1件と、その内訳を追加する。"""
    cursor = connection.execute(
        "INSERT INTO salary_history "
        "(employee_id, effective_from, effective_to, pay_system, employment_type, grade, "
        "annual_salary, revision_type, note, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.employee_id,
            record.effective_from.isoformat(),
            effective_to.isoformat() if effective_to else None,
            record.pay_system,
            record.employment_type,
            record.grade,
            record.annual_salary,
            record.revision_type,
            record.note,
            now,
            now,
        ),
    )
    history_id = cursor.lastrowid
    connection.executemany(
        "INSERT INTO salary_history_item "
        "(salary_history_id, pay_item_code, amount, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(history_id, code, amount, now, now) for code, amount in record.items],
    )
