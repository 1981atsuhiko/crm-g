"""データ取込の共通処理（移行設計書 3章・7章、アーキテクチャ設計書 `ARC-013`）。

- エラーの種類（`E001`〜`E019`。`MIG-060`）と、エラー1件の型（行番号・列名・種類）
- 取込用 CSV の読み込みと見出しの照合（`MIG-020`、`MIG-021`）
- セルの値の解釈（`MIG-022`〜`MIG-025`）

エラー・例外には、CSV の値（氏名・金額など）を含めない。行番号・列名・種類だけを持つ
（`MIG-005`、`SCD-023`、`SCD-042`）。標準ライブラリの `ValueError` などは、値を含むメッセージを
持つため、必ずエラーの種類に変換し、原因として連結しない。
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from fractions import Fraction

from crm_g.domain.period import half_code
from crm_g.infrastructure.csv_reader import CsvReadError, CsvReadFailure, read_csv


class ImportErrorCode(StrEnum):
    """取込のエラーの種類（移行設計書 `MIG-060`）。"""

    NOT_UTF8 = "E001"
    NO_HEADER = "E002"
    MISSING_COLUMN = "E003"
    UNKNOWN_COLUMN = "E004"
    DUPLICATE_COLUMN = "E005"
    REQUIRED_EMPTY = "E006"
    INVALID_FORMAT = "E007"
    NOT_IN_CHOICES = "E008"
    OUT_OF_RANGE = "E009"
    EMPLOYEE_NOT_FOUND = "E010"
    EMPLOYEE_AMBIGUOUS = "E011"
    DUPLICATE_KEY_IN_FILE = "E012"
    DUPLICATE_KEY_IN_DB = "E013"
    PERIOD_OVERLAP = "E014"
    REFERENCE_NOT_RESOLVED = "E015"
    INVALID_COMBINATION = "E016"
    INVALID_HISTORY_ORDER = "E017"
    OUT_OF_TARGET_PERIOD = "E018"
    ALREADY_CLOSED = "E019"

    @property
    def description(self) -> str:
        """画面・移行コマンドに出す、エラーの種類の説明。"""
        return _ERROR_DESCRIPTIONS[self]


_ERROR_DESCRIPTIONS: Mapping[ImportErrorCode, str] = {
    ImportErrorCode.NOT_UTF8: "文字コードが UTF-8 ではない",
    ImportErrorCode.NO_HEADER: "見出し行がない（空のファイル）、または CSV として読めない",
    ImportErrorCode.MISSING_COLUMN: "必須の列がない",
    ImportErrorCode.UNKNOWN_COLUMN: "定義にない列がある",
    ImportErrorCode.DUPLICATE_COLUMN: "見出しが重複している",
    ImportErrorCode.REQUIRED_EMPTY: "必須の値が空",
    ImportErrorCode.INVALID_FORMAT: "形式が正しくない",
    ImportErrorCode.NOT_IN_CHOICES: "値が選択肢にない",
    ImportErrorCode.OUT_OF_RANGE: "値の範囲・桁数が正しくない",
    ImportErrorCode.EMPLOYEE_NOT_FOUND: "社員が見つからない",
    ImportErrorCode.EMPLOYEE_AMBIGUOUS: "社員が複数見つかる",
    ImportErrorCode.DUPLICATE_KEY_IN_FILE: "ファイル内で、キーが重複している",
    ImportErrorCode.DUPLICATE_KEY_IN_DB: "DB に、すでに同じキーのデータがある",
    ImportErrorCode.PERIOD_OVERLAP: "期間が重なる",
    ImportErrorCode.REFERENCE_NOT_RESOLVED: "参照先が見つからない、または1件に決まらない",
    ImportErrorCode.INVALID_COMBINATION: "項目の組み合わせが正しくない",
    ImportErrorCode.INVALID_HISTORY_ORDER: "履歴の順序が正しくない",
    ImportErrorCode.OUT_OF_TARGET_PERIOD: "取込の対象期間の外",
    ImportErrorCode.ALREADY_CLOSED: "締め済み、または取込済みの行がある",
}


@dataclass(frozen=True)
class ImportIssue:
    """取込のエラー1件。値は持たない（`MIG-005`）。

    `line_number` は見出し行を 1 とするレコードの順番で、ファイル全体のエラーは `None`。
    `column` は列名（5章の定義の名称）で、特定の列に属さないエラーは `None`。
    """

    line_number: int | None
    column: str | None
    code: ImportErrorCode


class InvalidCellError(RuntimeError):
    """セルの値を解釈できないときのエラー。メッセージには、エラーの種類だけを入れる。"""

    def __init__(self, code: ImportErrorCode) -> None:
        """エラーの種類を保持する。"""
        super().__init__(f"セルの値を解釈できません（{code.value}）")
        self.code = code


# 除外列の一覧（移行設計書 `MIG-021` の暫定の一覧。`TBD:` 14章-5）。
EXCLUDED_COLUMNS = ("パスワード", "支払額", "差額（減額）", "正誤判定", "旧制度の等級", "売上目標")


# ---------------------------------------------------------------------------
# 文字列の正規化（MIG-021、MIG-022）
# ---------------------------------------------------------------------------


def normalize_cell(raw: str) -> str | None:
    """区分値・日付・金額などのセルを、NFKC 正規化と前後の空白の除去をする。空は `None`。"""
    text = unicodedata.normalize("NFKC", raw).strip()
    return text or None


def normalize_free_text(raw: str) -> str | None:
    """自由記述（備考・顧客名・氏名など）のセルを、前後の空白の除去だけをする。空は `None`。"""
    text = raw.strip()
    return text or None


_FORMAT_CATEGORY = "Cf"


def _normalize_header(raw: str) -> str:
    """見出しを、NFKC 正規化・書式文字の除去・前後の空白の除去をした値にする（`MIG-021`）。

    書式文字（Unicode の一般カテゴリ `Cf`。2つ目の BOM、ゼロ幅スペースなど）は、
    位置を問わず取り除く。
    """
    composed = unicodedata.normalize("NFKC", raw)
    kept = "".join(
        character for character in composed if unicodedata.category(character) != _FORMAT_CATEGORY
    )
    return kept.strip()


# ---------------------------------------------------------------------------
# 見出しの照合（MIG-021）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnLayout:
    """取込の種類ごとの列の定義。

    `defined` は読み込む列（必須の列を含む。給与項目マスタの名称も、呼び出し側が加える）。
    `required` は必須の列。`required_one_of` は、少なくとも1つが必要な列の組
    （例：氏名とメールアドレス。移行設計書 4.2）。`excluded` は読み捨てる列。

    `defined` と `excluded` を合わせた列名に、見出しと同じ正規化をした後で同じになるものが
    あるときは、`ValueError` にする（給与項目マスタの名称が既存の列名と重なる場合など）。
    """

    defined: tuple[str, ...]
    required: tuple[str, ...] = ()
    required_one_of: tuple[tuple[str, ...], ...] = ()
    excluded: tuple[str, ...] = EXCLUDED_COLUMNS

    def __post_init__(self) -> None:
        """正規化した後の列名の重複を検査する。メッセージには、列名を含めない。"""
        keys = [_normalize_header(name) for name in (*self.defined, *self.excluded)]
        if len(set(keys)) != len(keys):
            raise ValueError("列の定義に、正規化すると同じになる列名があります")


@dataclass(frozen=True)
class HeaderMatch:
    """見出しの照合の結果。

    `positions` は、定義の列名（`ColumnLayout.defined` の表記のまま）から、CSV の列の位置への対応。
    `excluded_columns` は、読み捨てた列の名称（正規化した見出し。画面の「取り込まなかった列」）。
    `excluded_positions` は、読み捨てた列の位置。
    """

    positions: Mapping[str, int]
    excluded_columns: tuple[str, ...]
    excluded_positions: frozenset[int]
    issues: tuple[ImportIssue, ...]


_HEADER_LINE_NUMBER = 1


def match_headers(headers: Sequence[str], layout: ColumnLayout) -> HeaderMatch:
    """見出し行を、列の定義と照合する（`MIG-021`）。

    重複は `E005`、定義にも除外列にもない列は `E004`、必須の列がないときは `E003` にする。
    空の見出しの列は、読まない（値があるときは、行の読み込みで `E004` にする）。

    定義の列にも除外列にも1つも一致しないときは、見出し行がない（1行目がデータの行）とみなし、
    ファイル全体の `E002`（行番号・列名なし）だけを返す。見出しの文字列がデータの値である
    おそれがあるため、`E004`・`E005` の列名は、少なくとも1列が一致したときにだけ出す（`MIG-005`）。
    """
    defined_by_key = {_normalize_header(name): name for name in layout.defined}
    excluded_keys = {_normalize_header(name) for name in layout.excluded}

    issues: list[ImportIssue] = []
    positions: dict[str, int] = {}
    excluded_columns: list[str] = []
    excluded_positions: set[int] = set()
    seen: set[str] = set()
    for position, raw in enumerate(headers):
        key = _normalize_header(raw)
        if not key:
            continue
        if key in seen:
            issues.append(ImportIssue(_HEADER_LINE_NUMBER, key, ImportErrorCode.DUPLICATE_COLUMN))
            continue
        seen.add(key)
        if key in defined_by_key:
            positions[defined_by_key[key]] = position
        elif key in excluded_keys:
            excluded_columns.append(key)
            excluded_positions.add(position)
        else:
            issues.append(ImportIssue(_HEADER_LINE_NUMBER, key, ImportErrorCode.UNKNOWN_COLUMN))

    if not positions and not excluded_positions:
        return HeaderMatch(
            positions={},
            excluded_columns=(),
            excluded_positions=frozenset(),
            issues=(ImportIssue(None, None, ImportErrorCode.NO_HEADER),),
        )

    issues.extend(
        ImportIssue(_HEADER_LINE_NUMBER, name, ImportErrorCode.MISSING_COLUMN)
        for name in layout.required
        if name not in positions
    )
    issues.extend(
        ImportIssue(_HEADER_LINE_NUMBER, "／".join(group), ImportErrorCode.MISSING_COLUMN)
        for group in layout.required_one_of
        if not any(name in positions for name in group)
    )
    return HeaderMatch(
        positions=positions,
        excluded_columns=tuple(excluded_columns),
        excluded_positions=frozenset(excluded_positions),
        issues=tuple(issues),
    )


# ---------------------------------------------------------------------------
# 取込用 CSV の読み込み（MIG-020）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportRow:
    """取込用 CSV の1行。`values` は、定義の列名からセルの文字列（正規化前）への対応。

    CSV に列がない定義の列は、含めない。除外列の値は、持たない（`MIG-021`）。
    """

    line_number: int
    values: Mapping[str, str]


@dataclass(frozen=True)
class ImportCsv:
    """取込用 CSV を読み込んだ結果。`issues` が空でなければ、取り込まない。

    `rows` は空行を除いた行（`MIG-061` の読み込んだ行数）。ファイル全体・見出しの
    エラーがあるときは、行を読まない（空のまま）。
    """

    rows: tuple[ImportRow, ...] = ()
    excluded_columns: tuple[str, ...] = ()
    issues: tuple[ImportIssue, ...] = ()


_FILE_ISSUE_CODES: Mapping[CsvReadFailure, ImportErrorCode] = {
    CsvReadFailure.NOT_UTF8: ImportErrorCode.NOT_UTF8,
    CsvReadFailure.MALFORMED: ImportErrorCode.NO_HEADER,
}


def read_import_csv(content: bytes, layout: ColumnLayout) -> ImportCsv:
    """取込用 CSV のバイト列を読み込み、見出しを照合して、行の並びにする（`MIG-020`、`MIG-021`）。

    UTF-8 として復号できないときは `E001`、空のファイル・見出し行が空・見出し行が定義の列にも
    除外列にも1つも一致しない・CSV として読めない（NUL 文字、閉じていない引用符、
    128KiB を超えるセルなど）ときは `E002` を、行番号なしで返す。
    """
    try:
        records = read_csv(content)
    except CsvReadError as error:
        return ImportCsv(issues=(ImportIssue(None, None, _FILE_ISSUE_CODES[error.failure]),))

    if not records or _is_blank(records[0].cells):
        return ImportCsv(issues=(ImportIssue(None, None, ImportErrorCode.NO_HEADER),))

    header = match_headers(records[0].cells, layout)
    if header.issues:
        return ImportCsv(excluded_columns=header.excluded_columns, issues=header.issues)

    known_positions = set(header.positions.values()) | header.excluded_positions
    rows: list[ImportRow] = []
    issues: list[ImportIssue] = []
    for record in records[1:]:
        if _is_blank(record.cells):
            continue
        rows.append(
            ImportRow(
                line_number=record.line_number,
                values={
                    name: record.cells[position] if position < len(record.cells) else ""
                    for name, position in header.positions.items()
                },
            )
        )
        # 見出しのない位置（空の見出し・見出しより右）のセルに値があるときは、定義にない列とする。
        if any(
            cell.strip()
            for position, cell in enumerate(record.cells)
            if position not in known_positions
        ):
            issues.append(ImportIssue(record.line_number, None, ImportErrorCode.UNKNOWN_COLUMN))
    return ImportCsv(
        rows=tuple(rows), excluded_columns=header.excluded_columns, issues=tuple(issues)
    )


def _is_blank(cells: Iterable[str]) -> bool:
    """すべてのセルが空（前後の空白を除いて）なら真（空行。`MIG-020`）。"""
    return not any(cell.strip() for cell in cells)


def issue_for_cell(line_number: int, column: str, error: InvalidCellError) -> ImportIssue:
    """セルの解釈のエラーを、行番号と列名を付けたエラーにする。"""
    return ImportIssue(line_number, column, error.code)


# ---------------------------------------------------------------------------
# セルの値の解釈（MIG-022〜MIG-025）
# 引数は、normalize_cell で正規化した、空でない文字列とする。
# ---------------------------------------------------------------------------


def require_value[T](value: T | None) -> T:
    """必須の値が空（`None`）なら、`E006` にする（`MIG-022`）。"""
    if value is None:
        raise InvalidCellError(ImportErrorCode.REQUIRED_EMPTY)
    return value


def match_choice[T](text: str, choices: Mapping[str, T]) -> T:
    """区分値を、選択肢の表記（同じ正規化をした値）と完全一致で照合する（`MIG-022`）。

    一致しないときは `E008`。`choices` は、表記から返す値への対応（マスタのコードなど）。
    """
    for label, value in choices.items():
        if normalize_cell(label) == text:
            return value
    raise InvalidCellError(ImportErrorCode.NOT_IN_CHOICES)


_DATE_PATTERN = re.compile(r"([0-9]{4})([-/])([0-9]{1,2})\2([0-9]{1,2})")
_YEAR_MONTH_PATTERN = re.compile(r"([0-9]{4})[-/]([0-9]{1,2})")
_HALF_CODE_PATTERN = re.compile(r"([0-9]{4})-H([12])")
_HALF_LABEL_PATTERN = re.compile(r"([0-9]{1,3})期([上下])期")
_FISCAL_YEAR_BASE = 2006
_HALF_CODE_MIN_YEAR = 1000
_HALF_LABEL_MIN_TERM = 1


def parse_date(text: str, *, latest: date | None = None) -> date:
    """日付（`YYYY-MM-DD`、`YYYY/MM/DD`。月・日は1桁でもよい）を解釈する（`MIG-023`）。

    形式の誤り・存在しない日付は `E007`。`latest` より後の日付は `E009`（生年月日の未来など）。
    """
    matched = _DATE_PATTERN.fullmatch(text)
    if matched is None:
        raise InvalidCellError(ImportErrorCode.INVALID_FORMAT)
    try:
        value = date(int(matched.group(1)), int(matched.group(3)), int(matched.group(4)))
    except ValueError:
        # ValueError のメッセージは値を含みうるため、連結しない。
        raise InvalidCellError(ImportErrorCode.INVALID_FORMAT) from None
    if latest is not None and value > latest:
        raise InvalidCellError(ImportErrorCode.OUT_OF_RANGE)
    return value


def parse_year_month(text: str) -> str:
    """年月（`YYYY-MM`、`YYYY/MM`。月は1桁でもよい）を、`YYYY-MM` にする（`MIG-023`）。"""
    matched = _YEAR_MONTH_PATTERN.fullmatch(text)
    if matched is None:
        raise InvalidCellError(ImportErrorCode.INVALID_FORMAT)
    year, month = int(matched.group(1)), int(matched.group(2))
    if year < 1 or not 1 <= month <= 12:
        raise InvalidCellError(ImportErrorCode.INVALID_FORMAT)
    return f"{year:04d}-{month:02d}"


def parse_half(text: str) -> str:
    """半期（`YYYY-H1`、`YYYY-H2`、`NN期上期`、`NN期下期`）を、半期コードにする（`MIG-023`）。

    `YYYY` は 1000〜9999 年、`NN期` は 1〜999 期で、年度は `NN + 2006`（`CLC-002`）。
    範囲外（`0001-H1`、`0期上期` など）・それ以外の形式は `E007`。
    """
    matched_code = _HALF_CODE_PATTERN.fullmatch(text)
    if matched_code is not None:
        fiscal_year, half = int(matched_code.group(1)), int(matched_code.group(2))
        if fiscal_year < _HALF_CODE_MIN_YEAR:
            raise InvalidCellError(ImportErrorCode.INVALID_FORMAT)
        return half_code(fiscal_year, half)

    matched_label = _HALF_LABEL_PATTERN.fullmatch(text)
    if matched_label is None:
        raise InvalidCellError(ImportErrorCode.INVALID_FORMAT)
    term = int(matched_label.group(1))
    if term < _HALF_LABEL_MIN_TERM:
        raise InvalidCellError(ImportErrorCode.INVALID_FORMAT)
    half = 1 if matched_label.group(2) == "上" else 2
    return half_code(term + _FISCAL_YEAR_BASE, half)


# 先頭のマイナス記号（半角 `-`、U+2212、全角 `－`）。負数として、範囲の検査に回す。
_MINUS_SIGNS = "-\u2212\uff0d"

# 円単位の整数。桁区切りの `,` は、3桁ごとの位置だけを受け付ける。
_AMOUNT_PATTERN = re.compile(rf"([{_MINUS_SIGNS}]?)([0-9]{{1,3}}(?:,[0-9]{{3}})+|[0-9]+)")
_AMOUNT_MAX_DIGITS = 15
AMOUNT_MAXIMUM = 999_999_999_999_999


def parse_amount(text: str, *, minimum: int = 0, maximum: int = AMOUNT_MAXIMUM) -> int:
    """金額（円単位の整数。桁区切りの `,` 可）を解釈する（`MIG-024`）。

    小数・通貨記号・単位が付いた値など、形式の誤りは `E007`（丸めない）。
    数字（桁区切りを除く）が 15 桁を超える値・`minimum` 未満・`maximum` を超える値は `E009`。
    先頭のマイナス記号（`-`、U+2212、全角 `－`）は負数とし、既定の下限 0 では `E009` になる。
    桁数は `int()` の前に検査し、巨大な桁数の値でも例外を出さない。
    """
    matched = _AMOUNT_PATTERN.fullmatch(text)
    if matched is None:
        raise InvalidCellError(ImportErrorCode.INVALID_FORMAT)
    digits = matched.group(2).replace(",", "")
    if len(digits) > _AMOUNT_MAX_DIGITS:
        raise InvalidCellError(ImportErrorCode.OUT_OF_RANGE)
    value = int(digits)
    if matched.group(1):
        value = -value
    if not minimum <= value <= maximum:
        raise InvalidCellError(ImportErrorCode.OUT_OF_RANGE)
    return value


_RATE_PATTERN = re.compile(rf"([{_MINUS_SIGNS}]?)([0-9]+)(?:\.([0-9]+))?(%?)")
_RATE_MAX_INTEGER_DIGITS = 3
_PERCENT_MAX_DECIMALS = 2
_RATIO_MAX_DECIMALS = 4
_BP_PER_ONE = 10000


def parse_rate_bp(text: str) -> int:
    """率（`70%`、`70.00%`、`0.7`、`1`）を、万分率の整数にする（`MIG-025`）。

    `%` 付きは小数第2位まで、`%` なしは小数第4位まで。超える桁・整数部が 3 桁を超える値・
    1（100 %）を超える値・負数（先頭のマイナス記号 `-`、U+2212、全角 `－`）は `E009`（丸めない）。
    数値でない値は `E007`。変換は `Fraction` で行い、浮動小数点を使わない。
    桁数は `int()` の前に検査し、巨大な桁数の値でも例外を出さない。
    """
    matched = _RATE_PATTERN.fullmatch(text)
    if matched is None:
        raise InvalidCellError(ImportErrorCode.INVALID_FORMAT)
    minus_sign, integer_part, decimal_part, percent_sign = matched.groups()
    decimals = decimal_part or ""
    is_percent = percent_sign == "%"
    max_decimals = _PERCENT_MAX_DECIMALS if is_percent else _RATIO_MAX_DECIMALS
    if len(integer_part) > _RATE_MAX_INTEGER_DIGITS or len(decimals) > max_decimals:
        raise InvalidCellError(ImportErrorCode.OUT_OF_RANGE)

    value = Fraction(int(integer_part + decimals), 10 ** len(decimals))
    if is_percent:
        value /= 100
    if minus_sign and value != 0:
        raise InvalidCellError(ImportErrorCode.OUT_OF_RANGE)
    if value > 1:
        raise InvalidCellError(ImportErrorCode.OUT_OF_RANGE)
    # 桁数を制限したため、万分率は必ず整数になる。
    return int(value * _BP_PER_ONE)


def parse_email(text: str) -> str:
    """メールアドレスを、小文字にする（`TBL-001`、`MIG-031`）。

    `@` をちょうど1つ含み、空白を含まないことだけを検査し、違反は `E007`（`MIG-040`）。
    """
    if text.count("@") != 1 or any(character.isspace() for character in text):
        raise InvalidCellError(ImportErrorCode.INVALID_FORMAT)
    return text.lower()
