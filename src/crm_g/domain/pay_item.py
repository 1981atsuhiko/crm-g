"""給与項目・手当マスタ（データ設計書 TBL-005）。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PayItem:
    """給与項目・手当マスタの1行。"""

    code: str
    name: str
    category: str
    in_monthly_salary: bool
    cost_direct: bool
    deduction_order: int | None
    sort_order: int

    def __post_init__(self) -> None:
        if self.deduction_order is not None and self.deduction_order < 1:
            raise ValueError("deduction_order は1以上にしてください")
