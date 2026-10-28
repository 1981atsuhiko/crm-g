"""数値の表示形式（`UID-041`）。"""

from datetime import datetime
from fractions import Fraction

from crm_g.domain.rounding import percent_one_decimal
from crm_g.infrastructure.datetime_utils import JST

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


def format_datetime(value: datetime) -> str:
    """日時を、日本時間の `YYYY/MM/DD HH:MM` で表示する（`UID-041`）。"""
    return value.astimezone(JST).strftime("%Y/%m/%d %H:%M")
