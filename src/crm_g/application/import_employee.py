"""`IMP-01` 社員の取込（移行設計書 5.1 `MIG-040`）。

取込用 CSV の列の定義・行ごとの検証・ファイル内の重複・DB との照合と、`employee` への
一括投入を行う。名寄せはしない（メールアドレスが主キーに当たる）。

- 既存の社員の更新はしない。追加だけを行う。DB にすでに同じメールアドレス・社員番号が
  あるときは `E013` にし、その CSV 全体を取り込まない（`MIG-003`）
- 投入は、既存の `create_employee` を使わない。1行ごとに操作ログ・再計算を行い、例外の
  メッセージに値を含むため。操作ログ・再計算・トランザクションは、呼び出し側（`data_import`）が
  受け持つ
- エラーには、セルの値を含めない（`MIG-005`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from crm_g.application.employee import EMPLOYMENT_TYPES, ENROLLMENT_STATUSES, GENDERS
from crm_g.application.import_common import (
    ColumnLayout,
    ImportErrorCode,
    ImportIssue,
    ImportRow,
    InvalidCellError,
    issue_for_cell,
    match_choice,
    normalize_cell,
    normalize_free_text,
    parse_date,
    parse_email,
    read_import_csv,
    require_value,
)
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now

EMAIL_COLUMN = "メールアドレス"
EMPLOYEE_NO_COLUMN = "社員番号"
NAME_COLUMN = "氏名"
NAME_KANA_COLUMN = "氏名カナ"
EMPLOYMENT_TYPE_COLUMN = "雇用形態"
BIRTH_DATE_COLUMN = "生年月日"
GENDER_COLUMN = "性別"
JOIN_DATE_COLUMN = "入社日"
NEAREST_STATION_COLUMN = "最寄駅"
RESIDENCE_AREA_COLUMN = "居住エリア"
JOB_TYPE_COLUMN = "職種"
ENROLLMENT_STATUS_COLUMN = "在籍ステータス"
RETIRE_DATE_COLUMN = "退職日"

EMPLOYEE_LAYOUT = ColumnLayout(
    defined=(
        EMAIL_COLUMN,
        EMPLOYEE_NO_COLUMN,
        NAME_COLUMN,
        NAME_KANA_COLUMN,
        EMPLOYMENT_TYPE_COLUMN,
        BIRTH_DATE_COLUMN,
        GENDER_COLUMN,
        JOIN_DATE_COLUMN,
        NEAREST_STATION_COLUMN,
        RESIDENCE_AREA_COLUMN,
        JOB_TYPE_COLUMN,
        ENROLLMENT_STATUS_COLUMN,
        RETIRE_DATE_COLUMN,
    ),
    required=(EMAIL_COLUMN, NAME_COLUMN, JOIN_DATE_COLUMN, ENROLLMENT_STATUS_COLUMN),
)
"""`IMP-01` の列の定義（`MIG-040`）。必須の列は、画面の登録（`SCN-022`）と同じ。"""

_ENROLLMENT_STATUS_CHOICES = {label: label for label in ENROLLMENT_STATUSES}
_EMPLOYMENT_TYPE_CHOICES = {label: label for label in EMPLOYMENT_TYPES}
_GENDER_CHOICES = {label: label for label in GENDERS}


@dataclass(frozen=True)
class EmployeeRecord:
    """取り込む社員1人分（検証を通った値。`employee` の列に対応する）。"""

    line_number: int
    email: str
    employee_no: str | None
    name: str
    name_kana: str | None
    employment_type: str | None
    birth_date: date | None
    gender: str | None
    join_date: date
    nearest_station: str | None
    residence_area: str | None
    job_type: str | None
    enrollment_status: str
    retire_date: date | None


@dataclass(frozen=True)
class EmployeeValidation:
    """社員の取込用 CSV の検証の結果。`issues` が空のときだけ、`records` を投入できる。

    `row_count` は、空行を除いた読み込んだ行数（`MIG-061`）。
    """

    row_count: int
    excluded_columns: tuple[str, ...]
    issues: tuple[ImportIssue, ...]
    records: tuple[EmployeeRecord, ...]


class _RowReader:
    """1行のセルを列ごとに解釈し、解釈できなかったセルのエラーを集める。"""

    def __init__(self, row: ImportRow) -> None:
        """解釈する行を保持する。"""
        self._row = row
        self.issues: list[ImportIssue] = []

    def read[T](self, column: str, parse: Callable[[str | None], T]) -> T | None:
        """列のセル（列がなければ `None`）を `parse` で解釈する。エラーなら記録して `None`。"""
        try:
            return parse(self._row.values.get(column))
        except InvalidCellError as error:
            self.issues.append(issue_for_cell(self._row.line_number, column, error))
            return None

    def add(self, column: str, code: ImportErrorCode) -> None:
        """セルの解釈以外のエラー（項目の組み合わせなど）を記録する。"""
        self.issues.append(ImportIssue(self._row.line_number, column, code))


def _free_text(raw: str | None) -> str | None:
    """自由記述のセル（前後の空白の除去だけ。`MIG-022`）。"""
    return None if raw is None else normalize_free_text(raw)


def _required_free_text(raw: str | None) -> str:
    """必須の自由記述のセル。空は `E006`。"""
    return require_value(_free_text(raw))


def _code_text(raw: str | None) -> str | None:
    """区分値・コードのセル（NFKC 正規化と前後の空白の除去。`MIG-022`）。"""
    return None if raw is None else normalize_cell(raw)


def _email(raw: str | None) -> str:
    """メールアドレスのセル。必須で、`@` を1つ含み空白を含まない。小文字にする（`MIG-040`）。"""
    return parse_email(require_value(_code_text(raw)))


def _choice(choices: Mapping[str, str], *, required: bool) -> Callable[[str | None], str | None]:
    """区分値のセルを、選択肢と照合する解釈の関数を作る（`E008`）。"""

    def parse(raw: str | None) -> str | None:
        text = _code_text(raw)
        if text is None:
            return require_value(text) if required else None
        return match_choice(text, choices)

    return parse


def _date(*, required: bool, latest: date | None = None) -> Callable[[str | None], date | None]:
    """日付のセルを解釈する関数を作る（`E007`。`latest` より後は `E009`）。"""

    def parse(raw: str | None) -> date | None:
        text = _code_text(raw)
        if text is None:
            return require_value(text) if required else None
        return parse_date(text, latest=latest)

    return parse


def _parse_row(row: ImportRow, today: date) -> tuple[EmployeeRecord | None, list[ImportIssue]]:
    """1行を解釈・検証する（行ごとの検証）。エラーがなければ、取り込む社員を返す。"""
    reader = _RowReader(row)
    email = reader.read(EMAIL_COLUMN, _email)
    employee_no = reader.read(EMPLOYEE_NO_COLUMN, _code_text)
    name = reader.read(NAME_COLUMN, _required_free_text)
    name_kana = reader.read(NAME_KANA_COLUMN, _free_text)
    employment_type = reader.read(
        EMPLOYMENT_TYPE_COLUMN, _choice(_EMPLOYMENT_TYPE_CHOICES, required=False)
    )
    # 生年月日が今日以降は、範囲の誤り（E009）。画面（SCN-022「今日より前」）に合わせる。
    birth_date = reader.read(
        BIRTH_DATE_COLUMN, _date(required=False, latest=today - timedelta(days=1))
    )
    gender = reader.read(GENDER_COLUMN, _choice(_GENDER_CHOICES, required=False))
    join_date = reader.read(JOIN_DATE_COLUMN, _date(required=True))
    nearest_station = reader.read(NEAREST_STATION_COLUMN, _free_text)
    residence_area = reader.read(RESIDENCE_AREA_COLUMN, _free_text)
    job_type = reader.read(JOB_TYPE_COLUMN, _free_text)
    enrollment_status = reader.read(
        ENROLLMENT_STATUS_COLUMN, _choice(_ENROLLMENT_STATUS_CHOICES, required=True)
    )
    retire_date = reader.read(RETIRE_DATE_COLUMN, _date(required=False))
    if retire_date is not None and join_date is not None and retire_date < join_date:
        reader.add(RETIRE_DATE_COLUMN, ImportErrorCode.INVALID_COMBINATION)

    # 必須の列は、エラーがなければ必ず値がある（None の判定は、型を絞るため）。
    if (
        reader.issues
        or email is None
        or name is None
        or join_date is None
        or enrollment_status is None
    ):
        return None, reader.issues
    record = EmployeeRecord(
        line_number=row.line_number,
        email=email,
        employee_no=employee_no,
        name=name,
        name_kana=name_kana,
        employment_type=employment_type,
        birth_date=birth_date,
        gender=gender,
        join_date=join_date,
        nearest_station=nearest_station,
        residence_area=residence_area,
        job_type=job_type,
        enrollment_status=enrollment_status,
        retire_date=retire_date,
    )
    return record, reader.issues


def _key_values(
    rows: Sequence[ImportRow], column: str, parse: Callable[[str], str]
) -> list[tuple[int, str]]:
    """重複の検査に使うキー（行番号と、解釈した値）を集める。解釈できない・空の行は除く。"""
    keys: list[tuple[int, str]] = []
    for row in rows:
        text = _code_text(row.values.get(column))
        if text is None:
            continue
        try:
            keys.append((row.line_number, parse(text)))
        except InvalidCellError:
            # 形式の誤りは、行ごとの検証で報告済み。
            continue
    return keys


def _duplicate_issues(
    keys: Iterable[tuple[int, str]], column: str, existing: frozenset[str]
) -> list[ImportIssue]:
    """ファイル内の重複（2回目以降の行を `E012`）と、DB との重複（`E013`）を検査する。"""
    issues: list[ImportIssue] = []
    seen: set[str] = set()
    for line_number, key in keys:
        if key in existing:
            issues.append(ImportIssue(line_number, column, ImportErrorCode.DUPLICATE_KEY_IN_DB))
        elif key in seen:
            issues.append(ImportIssue(line_number, column, ImportErrorCode.DUPLICATE_KEY_IN_FILE))
        seen.add(key)
    return issues


def _normalized_keys(values: Iterable[object]) -> frozenset[str]:
    """DB の値を、CSV のセルと同じ正規化（NFKC と前後の空白の除去）にそろえる。空は除く。"""
    keys: set[str] = set()
    for value in values:
        if value is None:
            continue
        key = normalize_cell(str(value))
        if key is not None:
            keys.add(key)
    return frozenset(keys)


def _existing_keys(connection: sqlite3.Connection) -> tuple[frozenset[str], frozenset[str]]:
    """DB の全社員（退職者を含む）の、メールアドレスと社員番号を返す。

    画面は社員番号を正規化せずに、メールアドレスを前後の空白の除去と小文字化だけで保存する
    ため、DB の値にも CSV と同じ正規化（メールアドレスはさらに小文字化）をかけて比べる（`E013`）。
    """
    rows = connection.execute("SELECT email, employee_no FROM employee").fetchall()
    emails = frozenset(key.lower() for key in _normalized_keys(row[0] for row in rows))
    employee_numbers = _normalized_keys(row[1] for row in rows)
    return emails, employee_numbers


def _identity(text: str) -> str:
    """社員番号は、正規化した値のまま比べる。"""
    return text


def validate_employee_csv(
    connection: sqlite3.Connection, content: bytes, today: date
) -> EmployeeValidation:
    """社員の取込用 CSV を読み込み、検証する（`MIG-040`、`MIG-050`）。DB は変更しない。

    行ごとの検証 → ファイル内の重複 → DB との照合の順に、すべてのエラーを集める。
    エラーは、行番号の順に並べる（ファイル全体のエラーが先）。
    """
    imported = read_import_csv(content, EMPLOYEE_LAYOUT)
    issues: list[ImportIssue] = list(imported.issues)
    records: list[EmployeeRecord] = []
    for row in imported.rows:
        record, row_issues = _parse_row(row, today)
        issues.extend(row_issues)
        if record is not None:
            records.append(record)

    if imported.rows:
        existing_emails, existing_numbers = _existing_keys(connection)
        issues.extend(
            _duplicate_issues(
                _key_values(imported.rows, EMAIL_COLUMN, parse_email), EMAIL_COLUMN, existing_emails
            )
        )
        issues.extend(
            _duplicate_issues(
                _key_values(imported.rows, EMPLOYEE_NO_COLUMN, _identity),
                EMPLOYEE_NO_COLUMN,
                existing_numbers,
            )
        )

    issues.sort(key=lambda issue: issue.line_number or 0)
    return EmployeeValidation(
        row_count=len(imported.rows),
        excluded_columns=imported.excluded_columns,
        issues=tuple(issues),
        records=tuple(records) if not issues else (),
    )


def insert_employees(connection: sqlite3.Connection, records: Sequence[EmployeeRecord]) -> int:
    """検証を通った社員を、`employee` に一括で追加する。追加した件数を返す。

    トランザクション・操作ログは、呼び出し側が受け持つ。
    """
    now = to_iso8601(utc_now())
    connection.executemany(
        "INSERT INTO employee "
        "(email, name, name_kana, employee_no, employment_type, birth_date, gender, "
        "join_date, nearest_station, residence_area, job_type, enrollment_status, "
        "retire_date, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                record.email,
                record.name,
                record.name_kana,
                record.employee_no,
                record.employment_type,
                record.birth_date.isoformat() if record.birth_date else None,
                record.gender,
                record.join_date.isoformat(),
                record.nearest_station,
                record.residence_area,
                record.job_type,
                record.enrollment_status,
                record.retire_date.isoformat() if record.retire_date else None,
                now,
                now,
            )
            for record in records
        ],
    )
    return len(records)
