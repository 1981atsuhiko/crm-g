"""単価の決定（CLC-013）。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RateHistoryEntry:
    """単価履歴の1行。案件単位の単価のときだけ assignment_id を持つ。"""

    assignment_id: int | None
    effective_from: date
    unit_price: int

    def __post_init__(self) -> None:
        if self.unit_price < 0:
            raise ValueError("unit_price は0以上にしてください")


def determine_unit_price(
    day: date, assignment_id: int | None, histories: Sequence[RateHistoryEntry]
) -> int | None:
    """営業日 day の単価を決める（CLC-013）。無ければ None を返す。

    稼働期間に assignment_id がある場合は、同じ assignment_id の単価履歴だけを見る。
    無い場合は、assignment_id が NULL の単価履歴（社員単位）だけを見る。
    """
    candidates = [
        history
        for history in histories
        if history.assignment_id == assignment_id and history.effective_from <= day
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda history: history.effective_from)
    return latest.unit_price
