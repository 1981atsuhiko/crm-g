"""名寄せの氏名の正規化（移行設計書 `MIG-030`、`REQ-MIG-001`）。

正規化名(s) = casefold( 取り除く( NFKC(s) ) )。取り除くのは、空白類（`str.isspace()` が
真の文字）と、書式文字（Unicode の一般カテゴリ `Cf`。ゼロ幅スペースなど）である。
異体字・旧字体の違い（`髙` と `高` など）は、吸収しない。
"""

import unicodedata

_FORMAT_CATEGORY = "Cf"


def normalized_name(text: str) -> str:
    """氏名・顧客名などを、照合のための正規化名にする。保存する値は変えない（`MGD-011`）。"""
    composed = unicodedata.normalize("NFKC", text)
    kept = "".join(
        character
        for character in composed
        if not character.isspace() and unicodedata.category(character) != _FORMAT_CATEGORY
    )
    return kept.casefold()
