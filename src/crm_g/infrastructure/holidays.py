"""祝日の取得（`ARC-012`）。

祝日は `jpholiday` から取得する。ドメイン層は `jpholiday` を直接呼ばないため、
取得処理はインフラ層のこの1か所に閉じ込める。
"""

from datetime import date

import jpholiday  # noqa: TID251 祝日の取得は、インフラ層のこの1か所に閉じ込める（ARC-012）


def year_holidays(year: int) -> frozenset[date]:
    """指定した年の祝日の集合を返す。"""
    return frozenset(day for day, _name in jpholiday.year_holidays(year))
