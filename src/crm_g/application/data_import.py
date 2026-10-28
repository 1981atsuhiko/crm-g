"""データ取込の確認・実行（移行設計書 6章 `MIG-050`〜`MIG-056`、7.2 `MIG-061`、`ARC-013`）。

画面（`SCN-070`）と移行コマンドは、この2つのユースケースを呼ぶ（`MIG-006`）。

- `check_import`（確認の実行）：読み込み・名寄せ・整形・検証だけを行い、DB を変更しない。
  操作ログも記録しない（`MIG-004`、`MIG-055`）
- `run_import`（取込の実行）：検証 → 取込前のバックアップ（`MIG-053`）→ `BEGIN IMMEDIATE` →
  読み込みから検証までのやり直し（DB を読み直す）→ 投入 → 再計算（`MIG-054`）→
  操作ログ `取込` を1件（`MIG-055`）→ コミット。エラーが1件でもあれば何も投入しない
  （`MIG-002`）。例外はロールバックして再送出する（`MIG-052`）

取込の種類ごとの処理は、`_DEFINITIONS` の登録表で差し替える。種類を足すときは、
`ImportKind` に識別子を足し、検証の関数（`ImportValidator`）を書いて登録表に加える。
検証の関数は、DB を変更せずに `ValidatedImport` を返す。その `insert` が、投入と、
再計算の対象（`RecalculationRequest`）を返す。

レポート・ログ・操作ログには、セルの値（氏名・金額など）を出さない（`MIG-005`、`SCD-023`、
`SCD-042`）。一致しなかった人の一覧（氏名を含む）は、レポートにだけ持たせる。
"""

import logging
import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from pathlib import Path

from crm_g.application.import_common import ImportIssue
from crm_g.application.import_employee import insert_employees, validate_employee_csv
from crm_g.application.import_matching import EmployeeMatch, UnmatchedPerson, unmatched_people
from crm_g.application.operation_log import Operation, OperationLogDetail, record_operation_log
from crm_g.application.recalculation import RecalculationResult, recalculate_range
from crm_g.infrastructure.backup import create_backup
from crm_g.infrastructure.db import transaction

_logger = logging.getLogger(__name__)

ERROR_DISPLAY_LIMIT = 1000
"""エラーの一覧に表示する件数の上限（`MGD-008`）。総数は別に表示する。"""


class ImportKind(StrEnum):
    """取込の種類の識別子（移行設計書 2.1 `MIG-010`。移行コマンドの引数）。"""

    EMPLOYEE = "employee"

    @property
    def label(self) -> str:
        """画面に出す、取込の種類の名称。"""
        return _DEFINITIONS[self].label


@dataclass(frozen=True)
class RecalculationRequest:
    """取込の後に再計算する社員と、最も早い月（`MIG-054`）。今月までを計算する。"""

    employee_ids: tuple[int, ...]
    since: date


@dataclass(frozen=True)
class InsertOutcome:
    """投入の結果。`recalculation` が `None` の種類は、再計算しない。"""

    imported_count: int
    recalculation: RecalculationRequest | None = None


@dataclass(frozen=True)
class ValidatedImport:
    """取込の種類ごとの検証の結果。

    `matches` は名寄せの結果で、名寄せをしない種類（`employee`）は `None`。
    `insert` は、`issues` が空のときだけ、取込の実行のトランザクションの中で呼ぶ。
    """

    row_count: int
    excluded_columns: tuple[str, ...]
    issues: tuple[ImportIssue, ...]
    insert: Callable[[sqlite3.Connection], InsertOutcome]
    matches: tuple[EmployeeMatch, ...] | None = None


ImportValidator = Callable[[sqlite3.Connection, bytes, date], ValidatedImport]
"""取込の種類ごとの検証の関数（接続、CSV のバイト列、今日）。DB を変更しない。"""


@dataclass(frozen=True)
class ImportKindDefinition:
    """取込の種類の登録表の1行。`target_table` は操作ログの対象テーブル（`MIG-055`）。"""

    label: str
    target_table: str
    validate: ImportValidator


@dataclass(frozen=True)
class ImportReport:
    """確認・取込のレポート（`MIG-061`）。

    `issues` は、すべてのエラー（行番号の順）。表示は `displayed_issues`（先頭の
    `ERROR_DISPLAY_LIMIT` 件）とし、総数（`error_count`）を併記する。
    `matched_row_count` は名寄せで一致した行数で、名寄せをしない種類は `None`。
    `executed` は、取り込んだ（コミットした）ときに真。`backup_path` は取込前のバックアップ。
    `recalculation` は、取込の後の再計算の結果で、再計算しない種類・取り込まなかったときは `None`。
    """

    kind: ImportKind
    row_count: int
    excluded_columns: tuple[str, ...]
    issues: tuple[ImportIssue, ...]
    matched_row_count: int | None = None
    unmatched: tuple[UnmatchedPerson, ...] = ()
    executed: bool = False
    imported_count: int = 0
    recalculation: RecalculationResult | None = None
    backup_path: Path | None = None

    @property
    def has_errors(self) -> bool:
        """エラーが1件以上あるか（あれば取り込まない）。"""
        return bool(self.issues)

    @property
    def error_count(self) -> int:
        """エラーの総数。"""
        return len(self.issues)

    @property
    def displayed_issues(self) -> tuple[ImportIssue, ...]:
        """表示するエラー（先頭の `ERROR_DISPLAY_LIMIT` 件。`MGD-008`）。"""
        return self.issues[:ERROR_DISPLAY_LIMIT]

    @property
    def recalculated_month_count(self) -> int:
        """取込の後に再計算した月数。"""
        return 0 if self.recalculation is None else len(self.recalculation.year_months)

    @property
    def calculation_error_count(self) -> int:
        """取込の後の再計算で出た、計算エラーの件数（取込のエラーではない。`MIG-052`）。"""
        return 0 if self.recalculation is None else len(self.recalculation.errors)


def _validate_employee(
    connection: sqlite3.Connection, content: bytes, today: date
) -> ValidatedImport:
    """`IMP-01` 社員の検証。社員の取込は、再計算しない（給与履歴がないため。`MIG-054`）。"""
    validation = validate_employee_csv(connection, content, today)

    def insert(target: sqlite3.Connection) -> InsertOutcome:
        return InsertOutcome(imported_count=insert_employees(target, validation.records))

    return ValidatedImport(
        row_count=validation.row_count,
        excluded_columns=validation.excluded_columns,
        issues=validation.issues,
        insert=insert,
    )


_DEFINITIONS: dict[ImportKind, ImportKindDefinition] = {
    ImportKind.EMPLOYEE: ImportKindDefinition(
        label="社員", target_table="employee", validate=_validate_employee
    ),
}


def _report(
    kind: ImportKind, validated: ValidatedImport, backup_path: Path | None = None
) -> ImportReport:
    """検証の結果から、レポートを作る。取り込んだときは、呼び出し側が取込の結果を加える。"""
    matches = validated.matches
    return ImportReport(
        kind=kind,
        row_count=validated.row_count,
        excluded_columns=validated.excluded_columns,
        issues=validated.issues,
        matched_row_count=None
        if matches is None
        else sum(1 for match in matches if match.employee_id is not None),
        unmatched=() if matches is None else unmatched_people(matches),
        backup_path=backup_path,
    )


def check_import(
    connection: sqlite3.Connection, kind: ImportKind, content: bytes, today: date
) -> ImportReport:
    """確認の実行（`MIG-004`）。読み込み・名寄せ・整形・検証だけを行い、DB を変更しない。

    `today` は今日の日付（生年月日の未来の判定などに使う）。
    """
    validated = _DEFINITIONS[kind].validate(connection, content, today)
    _logger.info(
        "取込の確認をしました（種類=%s 行数=%d エラー=%d）",
        kind.value,
        validated.row_count,
        len(validated.issues),
    )
    return _report(kind, validated)


class _ValidationFailedError(Exception):
    """トランザクションの中の再検証でエラーが出たとき、ロールバックするための内部の例外。"""

    def __init__(self, validated: ValidatedImport) -> None:
        """再検証の結果を保持する。"""
        super().__init__("取込の再検証でエラーがありました")
        self.validated = validated


def _operation_detail(
    kind: ImportKind, imported_count: int, recalculation: RecalculationResult | None
) -> str:
    """操作ログ `取込` の `detail`。件数だけを書く（`MIG-055`、`SCD-023`）。"""
    month_count = 0 if recalculation is None else len(recalculation.year_months)
    error_count = 0 if recalculation is None else len(recalculation.errors)
    return (
        f"種類={kind.value} 行数={imported_count} "
        f"再計算した月数={month_count} 計算エラー={error_count}"
    )


def run_import(  # noqa: PLR0913 取込前のバックアップの作成先（MIG-053）を受け取るため
    connection: sqlite3.Connection,
    kind: ImportKind,
    content: bytes,
    today: date,
    *,
    db_path: Path,
    backup_dir: Path,
) -> ImportReport:
    """取込の実行（`MIG-050`）。エラーがなければ投入し、エラーがあれば何も変えない。

    `db_path` は `connection` の DB ファイル、`backup_dir` は取込前のバックアップの作成先
    （`MIG-053`）。`today` は今日の日付で、再計算は今日の月までとする（`MIG-054`）。
    バックアップを作れないときは、例外を送出し、取り込まない。投入・再計算・操作ログの
    途中の例外は、ロールバックしてから再送出する（`MIG-052`）。
    """
    definition = _DEFINITIONS[kind]
    validated = definition.validate(connection, content, today)
    if validated.issues:
        _log_rejected(kind, validated)
        return _report(kind, validated)

    backup_path = create_backup(db_path, backup_dir)
    _logger.info("取込を開始しました（種類=%s 行数=%d）", kind.value, validated.row_count)
    try:
        with transaction(connection):
            # 確認の結果は使わず、BEGIN IMMEDIATE の後で DB を読み直して検証する。
            validated = definition.validate(connection, content, today)
            if validated.issues:
                raise _ValidationFailedError(validated)
            outcome = validated.insert(connection)
            recalculation = _recalculate(connection, outcome.recalculation, today)
            record_operation_log(
                connection,
                Operation.IMPORT,
                definition.target_table,
                OperationLogDetail(
                    detail=_operation_detail(kind, outcome.imported_count, recalculation)
                ),
            )
    except _ValidationFailedError as failure:
        _log_rejected(kind, failure.validated)
        return _report(kind, failure.validated, backup_path=backup_path)
    except Exception as error:
        # 例外のメッセージは値を含みうるため、ログには種類だけを出す（MIG-056）。
        _logger.error(
            "取込に失敗し、ロールバックしました（種類=%s 例外=%s）",
            kind.value,
            type(error).__name__,
        )
        raise

    _logger.info(
        "取込を終了しました（種類=%s 取り込んだ行数=%d 再計算した月数=%d 計算エラー=%d）",
        kind.value,
        outcome.imported_count,
        0 if recalculation is None else len(recalculation.year_months),
        0 if recalculation is None else len(recalculation.errors),
    )
    return replace(
        _report(kind, validated, backup_path),
        executed=True,
        imported_count=outcome.imported_count,
        recalculation=recalculation,
    )


def _recalculate(
    connection: sqlite3.Connection, request: RecalculationRequest | None, today: date
) -> RecalculationResult | None:
    """取込の後の再計算（`MIG-054`）。対象がない種類は、何もせず `None` を返す。"""
    if request is None or not request.employee_ids:
        return None
    return recalculate_range(connection, request.since, today, request.employee_ids)


def _log_rejected(kind: ImportKind, validated: ValidatedImport) -> None:
    """検証のエラーで取り込まなかったことを、件数だけでログに出す（`MIG-056`）。"""
    _logger.info(
        "検証のエラーがあるため、取り込みませんでした（種類=%s 行数=%d エラー=%d）",
        kind.value,
        validated.row_count,
        len(validated.issues),
    )
