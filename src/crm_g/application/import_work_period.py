"""`IMP-03` 稼働期間の取込（移行設計書 5.3 `MIG-042`）。

取込用 CSV の1行が、1件の稼働期間である。`work_period` と `assignment` に一括で投入する。

- 稼働状態は、稼働状態マスタの名称または旧名称と照合する（`E008`）
- 売上計上の稼働状態は、顧客名・区分が必須。売上計上でない稼働状態は、顧客名・案件名・
  区分を入れない。違反は `E016`。終了日が開始日より前も `E016`
- 稼働率は、`Fraction` で万分率に変換し、丸めない（`MIG-025`）。空は 100 %
- 同じ社員・同じ開始日の行が CSV 内にあるときは `E012`。同じ社員の期間が、CSV 内または
  DB の既存の稼働期間と重なるときは `E014`
- 参画は、（社員、顧客名、案件名、区分）でまとめ、DB に同じキーの参画があれば再利用して、
  期間を広げる（`MGD-004`）。DB に同じキーの参画が複数あるときは、1件に決まらないため
  `E015` とする
- 投入は、既存の `create_work_period`・`create_assignment` を使わない。1行ごとに操作ログ・
  再計算を行い、例外のメッセージに値を含むため。操作ログ・再計算・トランザクションは、
  呼び出し側（`data_import`）が受け持つ
- エラーには、セルの値を含めない（`MIG-005`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from crm_g.application.assignment import ASSIGNMENT_CATEGORIES
from crm_g.application.import_common import (
    ColumnLayout,
    ImportErrorCode,
    ImportIssue,
    ImportRow,
    parse_rate_bp,
    read_import_csv,
)
from crm_g.application.import_matching import EMAIL_COLUMN, NAME_COLUMN, EmployeeMatch
from crm_g.application.import_rows import (
    EMPLOYEE_KEY_REQUIRED_ONE_OF,
    RowReader,
    choice,
    code_text,
    free_text,
    match_rows,
    optional_date,
    required_choice,
    required_date,
    sorted_issues,
)
from crm_g.domain.name_normalization import normalized_name
from crm_g.domain.period_overlap import periods_overlap
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now

START_DATE_COLUMN = "開始日"
END_DATE_COLUMN = "終了日"
WORK_STATUS_COLUMN = "稼働状態"
CUSTOMER_NAME_COLUMN = "顧客名"
PROJECT_NAME_COLUMN = "案件名"
CATEGORY_COLUMN = "区分"
BILLING_RATIO_COLUMN = "稼働率"
NOTE_COLUMN = "備考"

WORK_PERIOD_LAYOUT = ColumnLayout(
    defined=(
        NAME_COLUMN,
        EMAIL_COLUMN,
        START_DATE_COLUMN,
        END_DATE_COLUMN,
        WORK_STATUS_COLUMN,
        CUSTOMER_NAME_COLUMN,
        PROJECT_NAME_COLUMN,
        CATEGORY_COLUMN,
        BILLING_RATIO_COLUMN,
        NOTE_COLUMN,
    ),
    required=(START_DATE_COLUMN, WORK_STATUS_COLUMN),
    required_one_of=EMPLOYEE_KEY_REQUIRED_ONE_OF,
)
"""`IMP-03` の列の定義（`MIG-042`）。"""

FULL_BILLING_RATIO_BP = 10000
"""稼働率が空のときの値（100 %。`TBL-016` の既定値）。"""

_CATEGORY_CHOICES = {label: label for label in ASSIGNMENT_CATEGORIES}


@dataclass(frozen=True)
class _WorkStatus:
    """稼働状態マスタの1行（照合に使う値だけ）。"""

    code: str
    is_billable: bool


@dataclass(frozen=True)
class AssignmentKey:
    """参画をまとめるキー（`MGD-004`）。顧客名・案件名は、正規化名（`MIG-030`）で比べる。

    案件名が空のときは、`project_key` を空文字列とする（空どうしを同じとみなす）。
    """

    employee_id: int
    customer_key: str
    project_key: str
    category: str


def assignment_key(
    employee_id: int, customer_name: str, project_name: str | None, category: str
) -> AssignmentKey:
    """参画の値から、まとめるキーを作る。"""
    return AssignmentKey(
        employee_id=employee_id,
        customer_key=normalized_name(customer_name),
        project_key=normalized_name(project_name) if project_name else "",
        category=category,
    )


@dataclass(frozen=True)
class WorkPeriodRecord:
    """取り込む稼働期間1件（検証を通った値）。`assignment` は、売上計上の行だけが持つ。"""

    line_number: int
    employee_id: int
    start_date: date
    end_date: date | None
    work_status_code: str
    billing_ratio_bp: int
    note: str | None
    assignment: AssignmentKey | None


@dataclass(frozen=True)
class AssignmentPlan:
    """取込で、作る・期間を広げる参画1件（`MGD-004`）。

    `existing_id` は、再利用する DB の参画の ID で、新しく作るときは `None`。
    `customer_name`・`project_name`・`category` は、新しく作るときに保存する値
    （キーで最初に現れた行の値）。`start_date`・`end_date` は、保存する期間。
    `changed` は、既存の参画の期間を変えるときに真。
    """

    key: AssignmentKey
    existing_id: int | None
    customer_name: str
    project_name: str | None
    category: str
    start_date: date
    end_date: date | None
    changed: bool = True


@dataclass(frozen=True)
class WorkPeriodValidation:
    """稼働期間の取込用 CSV の検証の結果。`issues` が空のときだけ、投入できる。

    `row_count` は、空行を除いた読み込んだ行数（`MIG-061`）。`matches` は名寄せの結果。
    """

    row_count: int
    excluded_columns: tuple[str, ...]
    issues: tuple[ImportIssue, ...]
    matches: tuple[EmployeeMatch, ...]
    records: tuple[WorkPeriodRecord, ...]
    assignments: tuple[AssignmentPlan, ...]


@dataclass(frozen=True)
class _AssignmentSource:
    """売上計上の行の、参画の値（キーと、保存する値）。"""

    key: AssignmentKey
    customer_name: str
    project_name: str | None
    category: str


@dataclass(frozen=True)
class _ParsedRow:
    """1行の解釈の結果。`record` は、名寄せと行ごとの検証を通ったときだけ持つ。

    `employee_id`・`start_date`・`end_date` は、ファイル内・DB との検証に使う。
    `has_period` は、開始日・終了日がともに正しいときに真（期間の重なりを検査する）。
    """

    line_number: int
    employee_id: int | None
    start_date: date | None
    end_date: date | None
    has_period: bool
    source: _AssignmentSource | None
    record: WorkPeriodRecord | None


@dataclass(frozen=True)
class _ExistingAssignment:
    """DB の既存の参画（まとめるキーと期間）。"""

    id: int
    key: AssignmentKey
    start_date: date
    end_date: date | None


def _work_status_choices(connection: sqlite3.Connection) -> dict[str, _WorkStatus]:
    """稼働状態マスタの名称・旧名称から、稼働状態への対応（`MIG-042`）。名称を優先する。"""
    rows = connection.execute(
        "SELECT code, name, legacy_name, is_billable FROM work_status ORDER BY sort_order, code"
    ).fetchall()
    choices: dict[str, _WorkStatus] = {}
    for code, name, _legacy_name, is_billable in rows:
        choices[str(name)] = _WorkStatus(str(code), bool(is_billable))
    for code, _name, legacy_name, is_billable in rows:
        if legacy_name:
            choices.setdefault(str(legacy_name), _WorkStatus(str(code), bool(is_billable)))
    return choices


def _billing_ratio(raw: str | None) -> int:
    """稼働率のセル（`MIG-025`）。空は 100 %。"""
    text = code_text(raw)
    return FULL_BILLING_RATIO_BP if text is None else parse_rate_bp(text)


def _assignment_source(
    reader: RowReader,
    row: ImportRow,
    status: _WorkStatus | None,
    employee_id: int | None,
) -> _AssignmentSource | None:
    """稼働状態と、顧客名・案件名・区分の組み合わせを検査し、参画の値を返す（`E016`）。

    売上計上の稼働状態は、顧客名・区分が必須。売上計上でない稼働状態は、3列とも値を入れない。
    """
    customer_name = reader.read(CUSTOMER_NAME_COLUMN, free_text)
    project_name = reader.read(PROJECT_NAME_COLUMN, free_text)
    has_category = code_text(row.values.get(CATEGORY_COLUMN)) is not None
    if status is None:
        reader.read(CATEGORY_COLUMN, choice(_CATEGORY_CHOICES))
        return None
    if not status.is_billable:
        for column, has_value in (
            (CUSTOMER_NAME_COLUMN, customer_name is not None),
            (PROJECT_NAME_COLUMN, project_name is not None),
            (CATEGORY_COLUMN, has_category),
        ):
            if has_value:
                reader.add(column, ImportErrorCode.INVALID_COMBINATION)
        return None

    if customer_name is None:
        reader.add(CUSTOMER_NAME_COLUMN, ImportErrorCode.INVALID_COMBINATION)
    if not has_category:
        reader.add(CATEGORY_COLUMN, ImportErrorCode.INVALID_COMBINATION)
        return None
    category = reader.read(CATEGORY_COLUMN, required_choice(_CATEGORY_CHOICES))
    if customer_name is None or category is None or employee_id is None:
        return None
    return _AssignmentSource(
        key=assignment_key(employee_id, customer_name, project_name, category),
        customer_name=customer_name,
        project_name=project_name,
        category=category,
    )


def _parse_row(
    row: ImportRow, match: EmployeeMatch, statuses: Mapping[str, _WorkStatus]
) -> tuple[_ParsedRow, list[ImportIssue]]:
    """1行を解釈・検証する（行ごとの検証）。名寄せのエラーも、この行のエラーに含める。"""
    reader = RowReader(row)
    if match.issue is not None:
        reader.issues.append(match.issue)
    start_date = reader.read(START_DATE_COLUMN, required_date)
    end_issue_count = len(reader.issues)
    end_date = reader.read(END_DATE_COLUMN, optional_date)
    has_period = start_date is not None and len(reader.issues) == end_issue_count
    if start_date is not None and end_date is not None and end_date < start_date:
        reader.add(END_DATE_COLUMN, ImportErrorCode.INVALID_COMBINATION)
        has_period = False
    status = reader.read(WORK_STATUS_COLUMN, required_choice(statuses))
    source = _assignment_source(reader, row, status, match.employee_id)
    billing_ratio_bp = reader.read(BILLING_RATIO_COLUMN, _billing_ratio)
    note = reader.read(NOTE_COLUMN, free_text)

    record: WorkPeriodRecord | None = None
    # 必須の列は、エラーがなければ必ず値がある（None の判定は、型を絞るため）。
    if not (
        reader.issues
        or match.employee_id is None
        or start_date is None
        or status is None
        or billing_ratio_bp is None
    ):
        record = WorkPeriodRecord(
            line_number=row.line_number,
            employee_id=match.employee_id,
            start_date=start_date,
            end_date=end_date,
            work_status_code=status.code,
            billing_ratio_bp=billing_ratio_bp,
            note=note,
            assignment=None if source is None else source.key,
        )
    parsed = _ParsedRow(
        line_number=row.line_number,
        employee_id=match.employee_id,
        start_date=start_date,
        end_date=end_date,
        has_period=has_period,
        source=source,
        record=record,
    )
    return parsed, reader.issues


def _existing_periods(
    connection: sqlite3.Connection, employee_ids: set[int]
) -> dict[int, list[tuple[date, date | None]]]:
    """社員ごとの、DB の既存の稼働期間（開始日、終了日）。"""
    periods: dict[int, list[tuple[date, date | None]]] = {}
    for employee_id in sorted(employee_ids):
        rows = connection.execute(
            "SELECT start_date, end_date FROM work_period WHERE employee_id = ? "
            "ORDER BY start_date",
            (employee_id,),
        ).fetchall()
        periods[employee_id] = [
            (date.fromisoformat(row[0]), date.fromisoformat(row[1]) if row[1] else None)
            for row in rows
        ]
    return periods


def _period_issues(
    parsed_rows: Sequence[_ParsedRow], existing: Mapping[int, Sequence[tuple[date, date | None]]]
) -> list[ImportIssue]:
    """ファイル内の重複（`E012`）と、期間の重なり（CSV 内・DB。`E014`）を検査する。

    CSV 内は、前の行と比べ、後の行をエラーにする。同じ開始日の行は `E012` だけとする。
    重なりの `E014` は、1行につき1件とする。
    """
    issues: list[ImportIssue] = []
    earlier: dict[int, list[_ParsedRow]] = {}
    for parsed in parsed_rows:
        if parsed.employee_id is None or parsed.start_date is None:
            continue
        others = earlier.setdefault(parsed.employee_id, [])
        if any(other.start_date == parsed.start_date for other in others):
            issues.append(
                ImportIssue(
                    parsed.line_number, START_DATE_COLUMN, ImportErrorCode.DUPLICATE_KEY_IN_FILE
                )
            )
        elif parsed.has_period and _overlaps(parsed, others, existing.get(parsed.employee_id, ())):
            issues.append(
                ImportIssue(parsed.line_number, START_DATE_COLUMN, ImportErrorCode.PERIOD_OVERLAP)
            )
        others.append(parsed)
    return issues


def _overlaps(
    parsed: _ParsedRow,
    others: Sequence[_ParsedRow],
    existing: Sequence[tuple[date, date | None]],
) -> bool:
    """行の期間が、CSV の前の行、または DB の既存の稼働期間と重なるか。"""
    assert parsed.start_date is not None  # noqa: S101 呼び出し側で開始日のある行に絞っている
    candidates = [
        (other.start_date, other.end_date)
        for other in others
        if other.has_period and other.start_date is not None
    ]
    return any(
        periods_overlap(parsed.start_date, parsed.end_date, start, end)
        for start, end in (*candidates, *existing)
    )


def _existing_assignments(
    connection: sqlite3.Connection, employee_ids: set[int]
) -> dict[AssignmentKey, list[_ExistingAssignment]]:
    """DB の既存の参画を、まとめるキーごとに返す。"""
    grouped: dict[AssignmentKey, list[_ExistingAssignment]] = {}
    for employee_id in sorted(employee_ids):
        rows = connection.execute(
            "SELECT id, customer_name, project_name, category, start_date, end_date "
            "FROM assignment WHERE employee_id = ? ORDER BY id",
            (employee_id,),
        ).fetchall()
        for row in rows:
            key = assignment_key(employee_id, row[1], row[2], row[3])
            grouped.setdefault(key, []).append(
                _ExistingAssignment(
                    id=row[0],
                    key=key,
                    start_date=date.fromisoformat(row[4]),
                    end_date=date.fromisoformat(row[5]) if row[5] else None,
                )
            )
    return grouped


def _later_end(first: date | None, second: date | None) -> date | None:
    """2つの終了日の遅いほう。どちらかが空（継続中）なら空。"""
    if first is None or second is None:
        return None
    return max(first, second)


def _assignment_plans(
    parsed_rows: Sequence[_ParsedRow],
    existing: Mapping[AssignmentKey, Sequence[_ExistingAssignment]],
) -> tuple[list[AssignmentPlan], list[ImportIssue]]:
    """売上計上の行を、キーでまとめ、作る・期間を広げる参画を決める（`MGD-004`）。

    DB に同じキーの参画が複数あるときは、その行を `E015` にする。
    """
    sources: dict[AssignmentKey, list[_ParsedRow]] = {}
    for parsed in parsed_rows:
        if parsed.source is not None and parsed.start_date is not None:
            sources.setdefault(parsed.source.key, []).append(parsed)

    plans: list[AssignmentPlan] = []
    issues: list[ImportIssue] = []
    for key, rows in sources.items():
        matched = existing.get(key, ())
        if len(matched) > 1:
            issues.extend(
                ImportIssue(
                    row.line_number, CUSTOMER_NAME_COLUMN, ImportErrorCode.REFERENCE_NOT_RESOLVED
                )
                for row in rows
            )
            continue
        start_date = min(row.start_date for row in rows if row.start_date is not None)
        end_date = rows[0].end_date
        for row in rows[1:]:
            end_date = _later_end(end_date, row.end_date)
        first_source = rows[0].source
        assert first_source is not None  # noqa: S101 参画の値のある行だけを集めている
        if not matched:
            plans.append(
                AssignmentPlan(
                    key=key,
                    existing_id=None,
                    customer_name=first_source.customer_name,
                    project_name=first_source.project_name,
                    category=first_source.category,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
            continue
        current = matched[0]
        new_start = min(current.start_date, start_date)
        new_end = _later_end(current.end_date, end_date)
        plans.append(
            AssignmentPlan(
                key=key,
                existing_id=current.id,
                customer_name=first_source.customer_name,
                project_name=first_source.project_name,
                category=first_source.category,
                start_date=new_start,
                end_date=new_end,
                changed=(new_start, new_end) != (current.start_date, current.end_date),
            )
        )
    return plans, issues


def validate_work_period_csv(
    connection: sqlite3.Connection, content: bytes
) -> WorkPeriodValidation:
    """稼働期間の取込用 CSV を読み込み、名寄せ・検証する（`MIG-042`、`MIG-050`）。DB は変更しない。

    行ごとの検証（名寄せを含む）→ ファイル内の重複・期間の重なり → DB との照合の順に、
    すべてのエラーを集める。
    """
    imported = read_import_csv(content, WORK_PERIOD_LAYOUT)
    matches = match_rows(connection, imported.rows)
    statuses = _work_status_choices(connection) if imported.rows else {}

    issues: list[ImportIssue] = list(imported.issues)
    parsed_rows: list[_ParsedRow] = []
    for row, match in zip(imported.rows, matches, strict=True):
        parsed, row_issues = _parse_row(row, match, statuses)
        parsed_rows.append(parsed)
        issues.extend(row_issues)

    employee_ids = {parsed.employee_id for parsed in parsed_rows if parsed.employee_id is not None}
    issues.extend(_period_issues(parsed_rows, _existing_periods(connection, employee_ids)))
    plans, assignment_issues = _assignment_plans(
        parsed_rows, _existing_assignments(connection, employee_ids)
    )
    issues.extend(assignment_issues)

    records = tuple(parsed.record for parsed in parsed_rows if parsed.record is not None)
    return WorkPeriodValidation(
        row_count=len(imported.rows),
        excluded_columns=imported.excluded_columns,
        issues=sorted_issues(issues),
        matches=matches,
        records=records if not issues else (),
        assignments=tuple(plans) if not issues else (),
    )


def insert_work_periods(
    connection: sqlite3.Connection,
    records: Sequence[WorkPeriodRecord],
    assignments: Sequence[AssignmentPlan],
) -> int:
    """検証を通った参画・稼働期間を、一括で追加する。追加した稼働期間の件数を返す。

    参画は、新しく作るか、既存の参画の期間を広げる（`MGD-004`）。トランザクション・
    操作ログ・再計算は、呼び出し側が受け持つ。
    """
    now = to_iso8601(utc_now())
    assignment_ids: dict[AssignmentKey, int] = {}
    for plan in assignments:
        assignment_ids[plan.key] = _save_assignment(connection, plan, now)

    connection.executemany(
        "INSERT INTO work_period (employee_id, start_date, end_date, work_status_code, "
        "assignment_id, billing_ratio_bp, note, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                record.employee_id,
                record.start_date.isoformat(),
                record.end_date.isoformat() if record.end_date else None,
                record.work_status_code,
                None if record.assignment is None else assignment_ids[record.assignment],
                record.billing_ratio_bp,
                record.note,
                now,
                now,
            )
            for record in records
        ],
    )
    return len(records)


def _save_assignment(connection: sqlite3.Connection, plan: AssignmentPlan, now: str) -> int:
    """参画を1件、新しく作るか、既存の参画の期間を広げる。参画の ID を返す。"""
    end_date = plan.end_date.isoformat() if plan.end_date else None
    if plan.existing_id is not None:
        if plan.changed:
            connection.execute(
                "UPDATE assignment SET start_date = ?, end_date = ?, updated_at = ? WHERE id = ?",
                (plan.start_date.isoformat(), end_date, now, plan.existing_id),
            )
        return plan.existing_id
    cursor = connection.execute(
        "INSERT INTO assignment (employee_id, customer_name, project_name, category, "
        "start_date, end_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            plan.key.employee_id,
            plan.customer_name,
            plan.project_name,
            plan.category,
            plan.start_date.isoformat(),
            end_date,
            now,
            now,
        ),
    )
    new_id = cursor.lastrowid
    assert new_id is not None  # noqa: S101 AUTOINCREMENT主キーのINSERT直後のため必ず取得できる
    return new_id
