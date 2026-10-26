"""社員×月の表のセルの内容（`SCN-050` 稼働期間タブ、`CLC-011`）。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from crm_g.domain.business_days import is_within_period

UNSET_MARK = "（未設定あり）"


@dataclass(frozen=True)
class StatusPeriod:
    """稼働期間のうち、表示に必要な項目（開始日・終了日・稼働状態の名称）。"""

    start_date: date
    end_date: date | None
    status_name: str


def summarize_month(
    month_start: date,
    month_end: date,
    periods: Sequence[StatusPeriod],
    present_business_days: Sequence[date],
) -> str:
    """その月に含まれる稼働状態の名称を、日付順に「→」でつなぐ。

    同じ名称が続くときは1つにまとめる。在籍営業日のうち、どの稼働期間にも含まれない
    日があるときは、末尾に「（未設定あり）」を付ける（`CLC-011` の要確認）。
    """
    in_month = sorted(
        (
            period
            for period in periods
            if period.start_date <= month_end
            and (period.end_date is None or period.end_date >= month_start)
        ),
        key=lambda period: period.start_date,
    )
    names: list[str] = []
    for period in in_month:
        if not names or names[-1] != period.status_name:
            names.append(period.status_name)

    has_unset = any(
        not any(is_within_period(day, period.start_date, period.end_date) for period in in_month)
        for day in present_business_days
    )
    return "→".join(names) + (UNSET_MARK if has_unset else "")
