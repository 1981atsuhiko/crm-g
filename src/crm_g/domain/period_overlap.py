"""有効期間の重なり判定（データ設計書10章-9）。

有効期間付きのテーブル（`work_status_rate`、`pay_item`、`system_setting`、
`bonus_rule`）は、同じキーで期間が重ならないことを、アプリケーション層で守る
（DBでは守らない）。この判定はテーブルに依存しない、純粋な日付の計算のため、
ドメイン層に置く。
"""

from datetime import date


def periods_overlap(start_a: date, end_a: date | None, start_b: date, end_b: date | None) -> bool:
    """2つの期間が重なるかを判定する。`end` が `None` のときは、継続中として扱う。"""
    a_before_b_ends = end_b is None or start_a <= end_b
    b_before_a_ends = end_a is None or start_b <= end_a
    return a_before_b_ends and b_before_a_ends
