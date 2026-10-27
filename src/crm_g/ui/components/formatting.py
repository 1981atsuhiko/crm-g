"""数値の表示形式（`UID-041`）。"""

from fractions import Fraction

from crm_g.domain.rounding import percent_one_decimal

DASH = "－"


def format_yen(amount: int | None) -> str:
    """金額を3桁区切りで表示する。値がないときは「－」。"""
    return DASH if amount is None else f"{amount:,}"


def format_ratio_percent(ratio: Fraction | None) -> str:
    """比率を `%`・小数第1位で表示する（`UID-041`。粗利率・稼働人日率）。

    小数第2位を四捨五入する（0.05 は切り上げ。負の値は絶対値で四捨五入する）。
    値がない（売上が 0 など）ときは「－」。
    """
    if ratio is None:
        return DASH
    return f"{percent_one_decimal(ratio)}%"
