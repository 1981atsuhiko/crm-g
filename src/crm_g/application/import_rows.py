"""名寄せをする取込の種類（`IMP-02`〜`IMP-04`）に共通の、行の解釈と名寄せ（移行設計書 3章・4章）。

- 1行のセルを列ごとに解釈し、エラーを集める `RowReader`
- 空のセルを `None` とする、セルの解釈の関数（日付・金額・区分値・自由記述）
- 取込用 CSV の全行の名寄せ（`MIG-031`）

エラーには、セルの値を含めない（`MIG-005`、`SCD-023`、`SCD-042`）。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date

from crm_g.application.import_common import (
    ImportErrorCode,
    ImportIssue,
    ImportRow,
    InvalidCellError,
    issue_for_cell,
    match_choice,
    normalize_cell,
    normalize_free_text,
    parse_amount,
    parse_date,
    require_value,
)
from crm_g.application.import_matching import (
    EMAIL_COLUMN,
    NAME_COLUMN,
    EmployeeMatch,
    load_employee_directory,
    match_employee,
)

EMPLOYEE_KEY_REQUIRED_ONE_OF = ((NAME_COLUMN, EMAIL_COLUMN),)
"""社員の特定の列（氏名・メールアドレス）は、少なくとも1つが必要（移行設計書 5章）。"""


class RowReader:
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

    def add(self, column: str | None, code: ImportErrorCode) -> None:
        """セルの解釈以外のエラー（項目の組み合わせなど）を記録する。"""
        self.issues.append(ImportIssue(self._row.line_number, column, code))


def code_text(raw: str | None) -> str | None:
    """区分値・日付・金額のセル（NFKC 正規化と前後の空白の除去。`MIG-022`）。空は `None`。"""
    return None if raw is None else normalize_cell(raw)


def free_text(raw: str | None) -> str | None:
    """自由記述のセル（前後の空白の除去だけ。`MIG-022`）。空は `None`。"""
    return None if raw is None else normalize_free_text(raw)


def optional_date(raw: str | None) -> date | None:
    """任意の日付のセル（`MIG-023`）。空は `None`、形式の誤りは `E007`。"""
    text = code_text(raw)
    return None if text is None else parse_date(text)


def required_date(raw: str | None) -> date:
    """必須の日付のセル。空は `E006`、形式の誤りは `E007`。"""
    return parse_date(require_value(code_text(raw)))


def optional_amount(raw: str | None) -> int | None:
    """任意の金額のセル（0 以上。`MIG-024`）。空は `None`。"""
    text = code_text(raw)
    return None if text is None else parse_amount(text)


def required_amount(raw: str | None) -> int:
    """必須の金額のセル（0 以上）。空は `E006`。"""
    return parse_amount(require_value(code_text(raw)))


def choice[T](choices: Mapping[str, T]) -> Callable[[str | None], T | None]:
    """区分値のセルを、選択肢と照合する解釈の関数を作る（`E008`）。空は `None`。"""

    def parse(raw: str | None) -> T | None:
        text = code_text(raw)
        return None if text is None else match_choice(text, choices)

    return parse


def required_choice[T](choices: Mapping[str, T]) -> Callable[[str | None], T]:
    """必須の区分値のセルを、選択肢と照合する解釈の関数を作る（空は `E006`、不一致は `E008`）。"""
    optional = choice(choices)

    def parse(raw: str | None) -> T:
        return require_value(optional(raw))

    return parse


def match_rows(
    connection: sqlite3.Connection, rows: Sequence[ImportRow]
) -> tuple[EmployeeMatch, ...]:
    """全行を名寄せする（`MIG-031`）。行がないときは、DB を読まない。"""
    if not rows:
        return ()
    directory = load_employee_directory(connection)
    return tuple(
        match_employee(
            directory, row.line_number, row.values.get(NAME_COLUMN), row.values.get(EMAIL_COLUMN)
        )
        for row in rows
    )


def sorted_issues(issues: Iterable[ImportIssue]) -> tuple[ImportIssue, ...]:
    """エラーを、行番号の順に並べる（ファイル全体のエラーが先。同じ行は、見つけた順）。"""
    return tuple(sorted(issues, key=lambda issue: issue.line_number or 0))
