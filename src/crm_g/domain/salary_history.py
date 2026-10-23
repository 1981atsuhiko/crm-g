"""給与履歴・月給合計（CLC-005）。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from crm_g.domain.pay_item import PayItem


@dataclass(frozen=True)
class SalaryHistoryItem:
    """給与履歴の内訳の1行。"""

    pay_item: PayItem
    amount: int

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("amount は0以上にしてください")


@dataclass(frozen=True)
class SalaryHistory:
    """給与履歴の1件。月給合計は、内訳の合計とする（CLC-005）。"""

    effective_from: date
    effective_to: date | None
    items: tuple[SalaryHistoryItem, ...]

    def __post_init__(self) -> None:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to は effective_from 以後にしてください")

    @property
    def monthly_salary(self) -> int:
        return sum(item.amount for item in self.items)


def select_effective_salary_history(
    histories: Sequence[SalaryHistory], basis_date: date
) -> SalaryHistory | None:
    """基準日に有効な給与履歴を選ぶ（CLC-005）。無ければ None を返す。"""
    candidates = [
        history
        for history in histories
        if history.effective_from <= basis_date
        and (history.effective_to is None or basis_date <= history.effective_to)
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        raise ValueError("基準日に有効な給与履歴が複数あります（期間が重複しています）")
    return candidates[0]
