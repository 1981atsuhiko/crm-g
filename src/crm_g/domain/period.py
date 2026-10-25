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
