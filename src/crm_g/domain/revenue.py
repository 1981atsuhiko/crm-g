"""売上の計算（CLC-012）。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from crm_g.domain.business_days import BusinessCalendar, is_present, is_within_period
from crm_g.domain.rate_history import RateHistoryEntry, determine_unit_price
from crm_g.domain.rounding import ceil_yen


@dataclass(frozen=True)
class BillingPeriod:
    """当月に重なる稼働期間のうち、売上の計算に必要な部分。

    `payroll.WorkPeriod` と同じ work_period テーブルの行を、売上の計算に
    必要な列（is_billable・billing_ratio・assignment_id）だけで表した別の型である。
    """

    start_date: date
    end_date: date | None
    is_billable: bool
    billing_ratio: Fraction
    assignment_id: int | None

    def __post_init__(self) -> None:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date は start_date 以後にしてください")
        if not 0 <= self.billing_ratio <= 1:
            raise ValueError("billing_ratio は0以上1以下にしてください（データ設計書 TBL-016）")

    def covers(self, day: date) -> bool:
        return is_within_period(day, self.start_date, self.end_date)


@dataclass(frozen=True)
class RevenueResult:
    """1社員・1か月の売上の計算結果。"""

    sales: int
    has_missing_unit_price: bool


def _find_billing_period(day: date, periods: Sequence[BillingPeriod]) -> BillingPeriod | None:
    for period in periods:
        if period.covers(day):
            return period
    return None


def calculate_revenue(
    *,
    business_calendar: BusinessCalendar,
    join_date: date | None,
    retire_date: date | None,
    billing_periods: Sequence[BillingPeriod],
    rate_histories: Sequence[RateHistoryEntry],
) -> RevenueResult:
    """1社員・1か月の売上を計算する（CLC-012、CLC-013）。

    売上計上（is_billable）の営業日に単価が決まらない場合は、has_missing_unit_price を
    True にする。そのときの sales は使わない。呼び出し側は、その社員・月を計算エラー
    として保存しない（計算仕様書 4.2・8章。単価がない案件は、単価0を登録する運用）。

    端数処理（切り上げ）は、社員・月の合計に対して1回だけ行う。稼働期間ごとには
    行わない（計算仕様書12章-6の暫定の扱い）。
    """
    if business_calendar.day_count == 0:
        raise ValueError("当月の営業日がありません")

    total = Fraction(0)
    has_missing_unit_price = False
    for day in business_calendar.business_days:
        if not is_present(day, join_date, retire_date):
            continue

        period = _find_billing_period(day, billing_periods)
        if period is None or not period.is_billable:
            continue

        unit_price = determine_unit_price(day, period.assignment_id, rate_histories)
        if unit_price is None:
            has_missing_unit_price = True
            continue

        total += Fraction(unit_price) * period.billing_ratio

    sales = ceil_yen(total / business_calendar.day_count)
    return RevenueResult(sales=sales, has_missing_unit_price=has_missing_unit_price)
