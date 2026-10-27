"""稼働人日率・待機者（CLC-025）。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from crm_g.domain.business_days import is_within_period

WAITING_STATUS_CODES = ("WAIT_FULL", "WAIT_CUT")


@dataclass(frozen=True)
class StatusSpan:
    """稼働期間のうち、稼働人日率・待機者の判定に必要な項目。"""

    start_date: date
    end_date: date | None
    status_code: str
    status_name: str
    is_billable: bool


@dataclass(frozen=True)
class WaitingSummary:
    """社員1人の、対象月の待機の要約。"""

    status_names: str
    day_count: int
    periods: tuple[tuple[date, date], ...]


def _days_in(span: StatusSpan, present_days: Sequence[date]) -> list[date]:
    return [day for day in present_days if is_within_period(day, span.start_date, span.end_date)]


def count_billable_man_days(present_days: Sequence[date], spans: Sequence[StatusSpan]) -> int:
    """在籍営業日のうち、売上計上が ○ の稼働期間に含まれる日の数を返す。"""
    billable = [span for span in spans if span.is_billable]
    return sum(
        1
        for day in present_days
        if any(is_within_period(day, span.start_date, span.end_date) for span in billable)
    )


def man_day_ratio(billable_man_days: int, present_man_days: int) -> Fraction | None:
    """稼働人日率を求める。在籍営業日の人日が 0 のときは算出しない（None）。"""
    if present_man_days == 0:
        return None
    return Fraction(billable_man_days, present_man_days)


def summarize_waiting(
    month_start: date,
    month_end: date,
    present_days: Sequence[date],
    spans: Sequence[StatusSpan],
) -> WaitingSummary | None:
    """対象月の待機の要約を返す。待機の在籍営業日がないときは None。

    待機の稼働状態の名称は、日付順に「→」でつなぐ（同じ名称が続くときは1つにまとめる）。
    期間は、待機の稼働期間を対象月の範囲に切ったもの。
    """
    waiting = sorted(
        (
            span
            for span in spans
            if span.status_code in WAITING_STATUS_CODES and _days_in(span, present_days)
        ),
        key=lambda span: span.start_date,
    )
    if not waiting:
        return None
    names: list[str] = []
    for span in waiting:
        if not names or names[-1] != span.status_name:
            names.append(span.status_name)
    days = {day for span in waiting for day in _days_in(span, present_days)}
    periods = tuple(
        (
            max(span.start_date, month_start),
            month_end if span.end_date is None else min(span.end_date, month_end),
        )
        for span in waiting
    )
    return WaitingSummary("→".join(names), len(days), periods)
