"""円未満の端数処理の試験（CLC-001）。"""

from fractions import Fraction

import pytest

from crm_g.domain.rounding import ceil_yen


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (Fraction(0), 0),
        (Fraction(5), 5),
        (Fraction(5, 2), 3),
        (Fraction(1, 19), 1),
    ],
)
def test_ceil_yen_examples(amount: Fraction, expected: int) -> None:
    assert ceil_yen(amount) == expected


def test_ceil_yen_rejects_negative_value() -> None:
    with pytest.raises(ValueError, match="0以上"):
        ceil_yen(Fraction(-1))
