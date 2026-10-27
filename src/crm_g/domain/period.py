"""期・半期の判定（CLC-002）。"""

from dataclasses import dataclass
from datetime import date

_FISCAL_YEAR_BASE = 2006


@dataclass(frozen=True)
class FiscalHalf:
    """年月から求めた、期・半期の情報。"""

    fiscal_year: int
    period: int
    half_name: str
    half_code: str


def fiscal_half(year: int, month: int) -> FiscalHalf:
    """年月から、年度・期・半期・半期コードを求める。"""
    if not 1 <= month <= 12:
        raise ValueError("month は1から12で指定してください")

    if month >= 4:
        fiscal_year = year
    else:
        fiscal_year = year - 1
    period = fiscal_year - _FISCAL_YEAR_BASE

    if 4 <= month <= 9:
        half_name = "上期"
        half_code = f"{fiscal_year}-H1"
    else:
        half_name = "下期"
        half_code = f"{fiscal_year}-H2"

    return FiscalHalf(
        fiscal_year=fiscal_year, period=period, half_name=half_name, half_code=half_code
    )


def half_date_range(fiscal_year: int, half: int) -> tuple[date, date]:
    """年度・半期（1または2）から、開始日・終了日を求める（CLC-002、`SCN-031`）。"""
    if half == 1:
        return date(fiscal_year, 4, 1), date(fiscal_year, 9, 30)
    if half == 2:
        return date(fiscal_year, 10, 1), date(fiscal_year + 1, 3, 31)
    raise ValueError("half は1または2で指定してください")


def fiscal_year_date_range(fiscal_year: int) -> tuple[date, date]:
    """年度（通期）から、開始日・終了日を求める（CLC-002、`SCN-031`）。"""
    return date(fiscal_year, 4, 1), date(fiscal_year + 1, 3, 31)


FIRST_SELECTABLE_FISCAL_YEAR = 2025


def current_fiscal_half(today: date) -> tuple[int, int]:
    """今日が属する、年度と半期（1または2）を返す（CLC-002）。"""
    fiscal_year = today.year if today.month >= 4 else today.year - 1
    half = 1 if 4 <= today.month <= 9 else 2
    return fiscal_year, half


def selectable_halves(
    today: date, first_fiscal_year: int = FIRST_SELECTABLE_FISCAL_YEAR
) -> tuple[tuple[int, int], ...]:
    """半期の選択肢を返す。first_fiscal_year の上期から、今日が属する半期の次まで（`UID-063`）。"""
    fiscal_year, half = current_fiscal_half(today)
    last = (fiscal_year, 2) if half == 1 else (fiscal_year + 1, 1)
    options: list[tuple[int, int]] = []
    current = (first_fiscal_year, 1)
    while current <= last:
        options.append(current)
        current = (current[0], 2) if current[1] == 1 else (current[0] + 1, 1)
    return tuple(options)


def fiscal_year_label(fiscal_year: int) -> str:
    """年度を「20期」の形で表す（CLC-002）。"""
    return f"{fiscal_year - _FISCAL_YEAR_BASE}期"


def half_label(fiscal_year: int, half: int) -> str:
    """半期を「20期 上期」の形で表す（`UID-063`）。"""
    if half not in (1, 2):
        raise ValueError("half は1または2で指定してください")
    return f"{fiscal_year_label(fiscal_year)} {'上期' if half == 1 else '下期'}"


def half_months(fiscal_year: int, half: int) -> tuple[tuple[int, int], ...]:
    """半期に含まれる6か月を、(年, 月) の昇順で返す。"""
    start, _ = half_date_range(fiscal_year, half)
    return tuple(
        (start.year + (start.month - 1 + offset) // 12, (start.month - 1 + offset) % 12 + 1)
        for offset in range(6)
    )


def previous_half(fiscal_year: int, half: int) -> tuple[int, int]:
    """1つ前の半期を返す（上期の前は、前の年度の下期）。"""
    return (fiscal_year, 1) if half == 2 else (fiscal_year - 1, 2)


def half_code(fiscal_year: int, half: int) -> str:
    """半期コード（`YYYY-H1`／`YYYY-H2`。データ設計書 `DBD-006`）を返す。"""
    return f"{fiscal_year}-H{half}"
