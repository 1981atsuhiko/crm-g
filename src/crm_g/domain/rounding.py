"""端数処理（CLC-001）と、比率の表示の丸め（画面設計書 `UID-041`）。"""

import math
from fractions import Fraction


def ceil_yen(amount: Fraction) -> int:
    """0以上の金額を、円未満を切り上げて整数にする。

    負の値は、計算仕様書2.1の対象外（想定の入力ではない）のため拒否する。
    """
    if amount < 0:
        raise ValueError("ceil_yen には0以上の値を渡してください")
    return math.ceil(amount)


def percent_one_decimal(ratio: Fraction) -> str:
    """比率を百分率の小数第1位までの文字列にする（`UID-041`。粗利率・稼働人日率）。

    小数第2位を四捨五入する（0.05 は切り上げ。負の値は絶対値で四捨五入する）。単位は付けない。
    """
    tenths = math.floor(abs(ratio) * 1000 + Fraction(1, 2))
    sign = "-" if ratio < 0 and tenths != 0 else ""
    return f"{sign}{tenths // 10}.{tenths % 10}"


def rate_four_decimals(rate_bp: int) -> str:
    """万分率の掛け率・原価係数を、小数第4位まで固定の文字列にする（`UID-041`）。単位は付けない。"""
    return f"{rate_bp // 10000}.{rate_bp % 10000:04d}"
