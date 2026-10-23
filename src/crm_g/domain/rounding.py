"""円未満の端数処理（CLC-001）。"""

import math
from fractions import Fraction


def ceil_yen(amount: Fraction) -> int:
    """0以上の金額を、円未満を切り上げて整数にする。

    負の値は、計算仕様書2.1の対象外（想定の入力ではない）のため拒否する。
    """
    if amount < 0:
        raise ValueError("ceil_yen には0以上の値を渡してください")
    return math.ceil(amount)
