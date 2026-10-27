"""データ取込の名寄せ（移行設計書 4章 `MIG-030`〜`MIG-032`、`REQ-MIG-001`、`REQ-MIG-002`）。

行にメールアドレスの値があれば、社員のメールアドレス（小文字化した値）と完全一致で照合する。
なければ、氏名の正規化名（`MIG-030`）で照合する。一致なしは `E010`、複数は `E011`。
名寄せの対象は、DB の全社員（退職者を含む）である。

一致しなかった人の一覧（`MIG-032`）は、氏名を含むため、画面と移行コマンドの標準出力にだけ出す。
ログには出さない（`MIG-005`）。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from crm_g.application.import_common import (
    ImportErrorCode,
    ImportIssue,
    InvalidCellError,
    normalize_cell,
    normalize_free_text,
    parse_email,
)
from crm_g.domain.name_normalization import normalized_name

NAME_COLUMN = "氏名"
EMAIL_COLUMN = "メールアドレス"
EMPLOYEE_KEY_COLUMNS = (NAME_COLUMN, EMAIL_COLUMN)


@dataclass(frozen=True)
class EmployeeCandidate:
    """名寄せの対象の社員1人分。"""

    employee_id: int
    name: str
    email: str


@dataclass(frozen=True)
class EmployeeDirectory:
    """名寄せの辞書。正規化名・メールアドレス（小文字）から、社員 ID への対応。"""

    ids_by_name: Mapping[str, tuple[int, ...]]
    ids_by_email: Mapping[str, tuple[int, ...]]


def build_employee_directory(candidates: Iterable[EmployeeCandidate]) -> EmployeeDirectory:
    """社員の一覧から、名寄せの辞書を作る（`MIG-031`）。"""
    ids_by_name: dict[str, list[int]] = {}
    ids_by_email: dict[str, list[int]] = {}
    for candidate in candidates:
        ids_by_name.setdefault(normalized_name(candidate.name), []).append(candidate.employee_id)
        ids_by_email.setdefault(candidate.email.strip().lower(), []).append(candidate.employee_id)
    return EmployeeDirectory(
        ids_by_name={key: tuple(ids) for key, ids in ids_by_name.items()},
        ids_by_email={key: tuple(ids) for key, ids in ids_by_email.items()},
    )


def load_employee_directory(connection: sqlite3.Connection) -> EmployeeDirectory:
    """DB の全社員（退職者を含む）から、名寄せの辞書を作る（`MIG-031`）。"""
    rows = connection.execute("SELECT id, name, email FROM employee ORDER BY id").fetchall()
    return build_employee_directory(
        EmployeeCandidate(employee_id=row[0], name=row[1], email=row[2]) for row in rows
    )


@dataclass(frozen=True)
class EmployeeMatch:
    """1行の名寄せの結果。一致したときは `employee_id`、しなかったときは `issue` を持つ。

    `name`・`email` は、照合に使った CSV の値（一致しなかった人の一覧に使う。ログに出さない）。
    """

    line_number: int
    employee_id: int | None
    issue: ImportIssue | None
    name: str | None
    email: str | None


def match_employee(
    directory: EmployeeDirectory,
    line_number: int,
    name_cell: str | None,
    email_cell: str | None,
) -> EmployeeMatch:
    """1行の氏名・メールアドレスのセルから、社員を特定する（`MIG-031`）。

    `name_cell`・`email_cell` は、セルの文字列（正規化前）。CSV にその列がないときは `None`。
    メールアドレスに値があれば、それだけで照合する（氏名は使わない。`MGD-001`）。
    両方とも空のときは `E006` にする。
    """
    name = None if name_cell is None else normalize_free_text(name_cell)
    email = None if email_cell is None else normalize_cell(email_cell)

    if email is not None:
        column = EMAIL_COLUMN
        try:
            ids = directory.ids_by_email.get(parse_email(email), ())
        except InvalidCellError:
            # 形式の誤ったメールアドレスは、どの社員とも一致しない。
            ids = ()
    elif name is not None:
        column = NAME_COLUMN
        ids = directory.ids_by_name.get(normalized_name(name), ())
    else:
        column = NAME_COLUMN if name_cell is not None else EMAIL_COLUMN
        issue = ImportIssue(line_number, column, ImportErrorCode.REQUIRED_EMPTY)
        return EmployeeMatch(line_number, None, issue, None, None)

    if len(ids) == 1:
        return EmployeeMatch(line_number, ids[0], None, name, email)
    code = ImportErrorCode.EMPLOYEE_NOT_FOUND if not ids else ImportErrorCode.EMPLOYEE_AMBIGUOUS
    return EmployeeMatch(line_number, None, ImportIssue(line_number, column, code), name, email)


@dataclass(frozen=True)
class UnmatchedPerson:
    """一致しなかった人の一覧の1行（`MIG-032`）。氏名・メールアドレスは CSV の値。"""

    name: str | None
    email: str | None
    line_numbers: tuple[int, ...]
    code: ImportErrorCode


_UNMATCHED_CODES = (ImportErrorCode.EMPLOYEE_NOT_FOUND, ImportErrorCode.EMPLOYEE_AMBIGUOUS)


def unmatched_people(matches: Iterable[EmployeeMatch]) -> tuple[UnmatchedPerson, ...]:
    """`E010`・`E011` の行を、氏名（とメールアドレス）・原因ごとにまとめる（`MIG-032`）。

    並びは、最初に現れた行の順とする。
    """
    grouped: dict[tuple[str | None, str | None, ImportErrorCode], list[int]] = {}
    for match in matches:
        if match.issue is None or match.issue.code not in _UNMATCHED_CODES:
            continue
        key = (match.name, match.email, match.issue.code)
        grouped.setdefault(key, []).append(match.line_number)
    return tuple(
        UnmatchedPerson(name=name, email=email, line_numbers=tuple(lines), code=code)
        for (name, email, code), lines in grouped.items()
    )
