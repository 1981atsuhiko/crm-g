"""昇給額・改定の登録・改定月の警告（CLC-016〜CLC-018）。"""

from datetime import date, timedelta

from crm_g.domain.salary_history import SalaryHistory

_WARNING_FREE_MONTHS = {
    "通常評価制度": (4, 10),
    "年俸制": (4,),
}
_NO_WARNING_REVISION_TYPES = ("入社", "制度移行")


def calculate_raise_amount(current: SalaryHistory, previous: SalaryHistory | None) -> int | None:
    """昇給額を求める（CLC-016）。直前の履歴が無い場合は、昇給額を表示しない（None）。"""
    if previous is None:
        return None
    return current.monthly_salary - previous.monthly_salary


def determine_previous_effective_to(
    new_effective_from: date, previous_effective_from: date
) -> date:
    """新しい履歴を登録するとき、直前の履歴の終了日を求める（CLC-017）。

    直前の履歴の effective_from が、新しい履歴の effective_from 以後の場合はエラーにする。
    """
    if previous_effective_from >= new_effective_from:
        raise ValueError(
            "直前の履歴の effective_from は、新しい履歴の effective_from より前にしてください"
        )
    return new_effective_from - timedelta(days=1)


def needs_revision_warning(pay_system: str, revision_type: str, effective_from_month: int) -> bool:
    """改定月以外への登録に、警告を出すか判定する（CLC-018）。登録自体は拒否しない。

    改定区分が「入社」・「制度移行」の登録は、警告の対象外とする。単価連動は、
    警告の対象月を定めない暫定の扱い（計算仕様書12章-11）のため、警告しない。
    """
    if not 1 <= effective_from_month <= 12:
        raise ValueError("effective_from_month は1から12で指定してください")

    if revision_type in _NO_WARNING_REVISION_TYPES:
        return False

    warning_free_months = _WARNING_FREE_MONTHS.get(pay_system)
    if warning_free_months is None:
        return False

    return effective_from_month not in warning_free_months
