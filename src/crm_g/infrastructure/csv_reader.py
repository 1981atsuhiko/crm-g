"""CSV の読み込み（アーキテクチャ設計書 `ARC-002` のインフラ層の「CSV入出力」、`ARC-013`）。

ファイルの形式は、移行設計書 `MIG-020` のとおり。UTF-8（BOM 付き・なし）、改行 CRLF・LF、
`,` 区切り、引用符 `"`。標準 `csv` の既定の方言に、壊れた引用符を検出する `strict` を加えて読む。
行番号は、見出し行を 1 として数えたレコードの順番（空行も数える）とする。

例外のメッセージには、CSV の値（氏名・金額など）を含めない（`SCD-023`、`SCD-042`、`MIG-005`）。
"""

import csv  # noqa: TID251 CSV の読み込みは、インフラ層のこの1か所に閉じ込める（ARC-002、ARC-013）
import io
from dataclasses import dataclass
from enum import Enum

# 表計算ソフトの CSV では使わない文字。UTF-16 などを UTF-8 として復号すると現れる。
_NUL = "\x00"


class CsvReadFailure(Enum):
    """CSV を読めなかった原因の種類。"""

    NOT_UTF8 = "not_utf8"
    MALFORMED = "malformed"


class CsvReadError(RuntimeError):
    """CSV を読めなかったときのエラー。メッセージには、原因の種類だけを入れる。"""

    def __init__(self, failure: CsvReadFailure) -> None:
        """原因の種類を保持する。"""
        super().__init__(f"CSV を読み込めませんでした（{failure.value}）")
        self.failure = failure


@dataclass(frozen=True)
class CsvRecord:
    """CSV の1レコード。`line_number` は見出し行を 1 とする順番。空行の `cells` は空。"""

    line_number: int
    cells: tuple[str, ...]


def read_csv(content: bytes) -> tuple[CsvRecord, ...]:
    """CSV のバイト列を、レコードの並びにする。見出し行も、1番目のレコードとして返す。

    UTF-8 として復号できないときは `CsvReadFailure.NOT_UTF8`、NUL 文字を含むときや、引用符が
    閉じていないなど CSV として解釈できないときは `CsvReadFailure.MALFORMED` の
    `CsvReadError` を送出する。
    """
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        # 復号の例外は、ファイルの内容（バイト列と位置）を保持するため、原因として連結しない。
        raise CsvReadError(CsvReadFailure.NOT_UTF8) from None

    if _NUL in text:
        raise CsvReadError(CsvReadFailure.MALFORMED)

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        return tuple(
            CsvRecord(line_number=line_number, cells=tuple(cells))
            for line_number, cells in enumerate(reader, start=1)
        )
    except csv.Error:
        # csv.Error のメッセージは値を含まないが、他の読み込みの失敗と同じ扱いにそろえる。
        raise CsvReadError(CsvReadFailure.MALFORMED) from None
