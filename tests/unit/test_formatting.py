"""数値の表示形式の試験（`UID-041`、`TC-SCN-025`）。"""

from fractions import Fraction

import pytest

from crm_g.ui.components.formatting import format_ratio_percent, format_yen


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (Fraction(1, 5), "20.0%"),
        (Fraction(235, 1000), "23.5%"),
        (Fraction(2345, 10000), "23.5%"),  # 0.05 は切り上げ
        (Fraction(2344, 10000), "23.4%"),
        (Fraction(-2345, 10000), "-23.5%"),  # 負の値は絶対値で四捨五入
        (Fraction(-1, 10000), "0.0%"),
        (Fraction(1), "100.0%"),
        (None, "－"),  # 売上が 0
    ],
)
def test_tc_scn_025_format_ratio_percent(ratio: Fraction | None, expected: str) -> None:
    assert format_ratio_percent(ratio) == expected


def test_format_yen() -> None:
    assert format_yen(1234567) == "1,234,567"
    assert format_yen(-5) == "-5"
    assert format_yen(None) == "－"
