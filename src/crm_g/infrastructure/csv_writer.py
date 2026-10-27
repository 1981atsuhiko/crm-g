"""CSV の書き出し（アーキテクチャ設計書 `ARC-002` のインフラ層の「CSV入出力」、`ARC-011`）。

ファイルの形式は、画面設計書 `UID-090` のとおり。UTF-8（BOM 付き）、改行 CRLF、`,` 区切り、
`csv.QUOTE_MINIMAL`。値なし（None）は空欄にする。
"""

import csv  # noqa: TID251 CSV の書き出しは、インフラ層のこの1か所に閉じ込める（ARC-002、ARC-011）
import io
from collections.abc import Iterable, Sequence


def write_csv(headers: Sequence[str], rows: Iterable[Sequence[str | int | None]]) -> bytes:
    """見出しと行から、CSV のバイト列を作る。"""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    # `utf-8-sig` は、先頭に BOM を付けて符号化する。
    return buffer.getvalue().encode("utf-8-sig")
