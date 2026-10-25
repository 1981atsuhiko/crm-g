"""期・半期の判定の試験（TC-CLC-011）。"""

from datetime import date

import pytest

from crm_g.domain.period import fiscal_half, fiscal_year_date_range, half_date_range


@pytest.mark.parametrize(
    ("year", "month", "fiscal_year", "period", "half_name", "half_code"),
    [
        (2025, 4, 2025, 19, "上期", "2025-H1"),
        (2026, 3, 2025, 19, "下期", "2025-H2"),
        (2026, 4, 2026, 20, "上期", "2026-H1"),
        (2026, 9, 2026, 20, "上期", "2026-H1"),
        (2026, 10, 2026, 20, "下期", "2026-H2"),
        (2027, 3, 2026, 20, "下期", "2026-H2"),
        (2027, 4, 2027, 21, "上期", "2027-H1"),
    ],
)
def test_tc_clc_011_fiscal_half_examples(  # noqa: PLR0913, PLR0917
    year: int, month: int, fiscal_year: int, period: int, half_name: str, half_code: str
) -> None:
    result = fiscal_half(year, month)

    assert result.fiscal_year == fiscal_year
    assert result.period == period
    assert result.half_name == half_name
    assert result.half_code == half_code


def test_fiscal_half_rejects_invalid_month() -> None:
    with pytest.raises(ValueError, match="month"):
        fiscal_half(2026, 13)


def test_half_date_range_first_half() -> None:
    assert half_date_range(2026, 1) == (date(2026, 4, 1), date(2026, 9, 30))


def test_half_date_range_second_half() -> None:
    assert half_date_range(2026, 2) == (date(2026, 10, 1), date(2027, 3, 31))


def test_half_date_range_rejects_invalid_half() -> None:
    with pytest.raises(ValueError, match="half"):
        half_date_range(2026, 3)


def test_fiscal_year_date_range() -> None:
    assert fiscal_year_date_range(2026) == (date(2026, 4, 1), date(2027, 3, 31))
