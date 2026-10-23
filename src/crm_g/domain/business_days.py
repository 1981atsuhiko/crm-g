"""営業日・在籍営業日（CLC-003、CLC-004）。"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class BusinessCalendar:
    """当月の営業日を、日付の昇順に並べた集合として持つ。"""

    business_days: tuple[date, ...]

    def __post_init__(self) -> None:
        days = self.business_days
        if len(set(days)) != len(days):
            raise ValueError("business_days に重複する日付があります")
        if list(days) != sorted(days):
            raise ValueError("business_days は日付の昇順で指定してください")

    @property
    def day_count(self) -> int:
        return len(self.business_days)


def is_present(day: date, join_date: date | None, retire_date: date | None) -> bool:
    """営業日 day に在籍しているか（CLC-004）。入社日・退職日の当日は在籍に含める。"""
    if join_date is not None and day < join_date:
        return False
    return retire_date is None or day <= retire_date


def present_business_days(
    calendar: BusinessCalendar, join_date: date | None, retire_date: date | None
) -> tuple[date, ...]:
    """当月の営業日のうち、在籍している日だけを取り出す。"""
    return tuple(day for day in calendar.business_days if is_present(day, join_date, retire_date))
