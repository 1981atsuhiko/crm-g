"""名寄せをする取込の種類に共通の、行の解釈と検証（移行設計書 3章・4章・5章）。

- 行のセルを列ごとに解釈し、エラーを集める（`RowReader`）
- 金額・年月・半期・区分値のセルの解釈の関数（`MIG-022`〜`MIG-024`）
- 行ごとの名寄せ（`MIG-031`）
- 主キーの重複の検査（ファイル内 `E012`、DB との照合）

エラーには、セルの値を含めない（`MIG-005`）。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence

from crm_g.application.import_common import (
    ImportErrorCode,
    ImportIssue,
    ImportRow,
    InvalidCellError,
    issue_for_cell,
    match_choice,
    normalize_cell,
    parse_amount,
    parse_half,
    parse_year_month,
    require_value,
)
from crm_g.application.import_matching import (
    EMAIL_COLUMN,
    NAME_COLUMN,
    EmployeeMatch,
    load_employee_directory,
    match_employee,
)

EMPLOYEE_KEY_COLUMN_GROUP = (NAME_COLUMN, EMAIL_COLUMN)
"""社員の特定の列（`IMP-02`〜`IMP-06` 共通。少なくとも1つが必要。移行設計書 5章）。"""


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

    def add(self, column: str, code: ImportErrorCode) -> None:
        """セルの解釈以外のエラー（項目の組み合わせ・対象期間など）を記録する。"""
        self.issues.append(ImportIssue(self._row.line_number, column, code))


# ---------------------------------------------------------------------------
# セルの解釈の関数（引数は正規化前のセル。列がないときは None）
# ---------------------------------------------------------------------------


def code_text(raw: str | None) -> str | None:
    """区分値・日付・金額のセル（NFKC 正規化と前後の空白の除去。`MIG-022`）。空は `None`。"""
    return None if raw is None else normalize_cell(raw)


def required_amount(raw: str | None) -> int:
    """必須の金額のセル（0 以上）。空は `E006`、形式の誤りは `E007`、負は `E009`。"""
    return parse_amount(require_value(code_text(raw)))


def amount_or_zero(raw: str | None) -> int:
    """任意の金額のセル（0 以上）。空は 0 とする（移行設計書 5.5 の暫定の扱い）。"""
    text = code_text(raw)
    return 0 if text is None else parse_amount(text)


def required_year_month(raw: str | None) -> str:
    """必須の年月のセル。`YYYY-MM` にする（`MIG-023`）。"""
    return parse_year_month(require_value(code_text(raw)))


def required_half(raw: str | None) -> str:
    """必須の半期のセル。半期コード（`YYYY-H1`／`YYYY-H2`）にする（`MIG-023`）。"""
    return parse_half(require_value(code_text(raw)))


def choice(choices: Iterable[str], *, required: bool) -> Callable[[str | None], str | None]:
    """区分値のセルを、選択肢の表記と照合する解釈の関数を作る（`E008`）。"""
    choice_map: Mapping[str, str] = {label: label for label in choices}

    def parse(raw: str | None) -> str | None:
        text = code_text(raw)
        if text is None:
            return require_value(text) if required else None
        return match_choice(text, choice_map)

    return parse


# ---------------------------------------------------------------------------
# 名寄せと重複の検査
# ---------------------------------------------------------------------------


def match_rows(
    connection: sqlite3.Connection, rows: Sequence[ImportRow]
) -> tuple[EmployeeMatch, ...]:
    """各行の氏名・メールアドレスから、社員を特定する（`MIG-031`）。行がなければ DB を読まない。"""
    if not rows:
        return ()
    directory = load_employee_directory(connection)
    return tuple(
        match_employee(
            directory,
            row.line_number,
            row.values.get(NAME_COLUMN),
            row.values.get(EMAIL_COLUMN),
        )
        for row in rows
    )


def key_issues[K: Hashable](
    keys: Iterable[tuple[int, K]],
    column: str,
    db_issue: Callable[[K], ImportErrorCode | None],
) -> list[ImportIssue]:
    """主キーの重複を検査する。

    `keys` は、行番号と主キーの組。`db_issue` は、DB の既存の行との照合で、エラーなら
    その種類を、取り込めるなら `None` を返す。DB との照合のエラーを優先し、そうでなければ、
    ファイル内の2回目以降の行を `E012` にする。
    """
    issues: list[ImportIssue] = []
    seen: set[K] = set()
    for line_number, key in keys:
        code = db_issue(key)
        if code is not None:
            issues.append(ImportIssue(line_number, column, code))
        elif key in seen:
            issues.append(ImportIssue(line_number, column, ImportErrorCode.DUPLICATE_KEY_IN_FILE))
        seen.add(key)
    return issues


def sorted_issues(issues: Iterable[ImportIssue]) -> tuple[ImportIssue, ...]:
    """エラーを、行番号の順に並べる（ファイル全体のエラーが先。同じ行は検出の順）。"""
    return tuple(sorted(issues, key=lambda issue: issue.line_number or 0))
