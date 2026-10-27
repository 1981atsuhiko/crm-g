"""名寄せの氏名の正規化の試験（移行設計書 `MIG-030`、TC-MIG-012、TC-MIG-013）。"""

import pytest

from crm_g.domain.name_normalization import normalized_name


@pytest.mark.parametrize(
    "text",
    [
        "山田\u3000太郎",
        "山田 太郎",
        "山田\t太郎",
        "山田\u00a0太郎",
        "山田\u200b太郎",
        " 山田太郎 ",
        "山田太郎",
    ],
)
def test_tc_mig_012_whitespace_and_format_characters_are_removed(text: str) -> None:
    """空白類（全角・半角・タブ・ノーブレークスペース）と書式文字を取り除く。"""
    assert normalized_name(text) == "山田太郎"


def test_tc_mig_013_half_width_kana_and_full_width_alphabet_are_unified() -> None:
    """半角カナは全角カナに、全角英字は半角小文字になる。"""
    assert normalized_name("ﾔﾏﾀﾞ ﾀﾛｳ") == normalized_name("ヤマダ タロウ")
    assert normalized_name("ＪＯＨＮ\u3000Ｓｍｉｔｈ") == "johnsmith"


def test_variant_kanji_are_not_unified() -> None:
    """異体字は吸収しない（`髙` と `高`）。"""
    assert normalized_name("髙橋") != normalized_name("高橋")
