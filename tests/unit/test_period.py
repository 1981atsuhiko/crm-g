"""期・半期の判定の試験（TC-CLC-011）。"""

from datetime import date

import pytest

from crm_g.domain.period import (
    current_fiscal_half,
    fiscal_half,
    fiscal_year_date_range,
    fiscal_year_label,
    half_date_range,
    half_label,
    half_months,
    selectable_halves,
)


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


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 3, 31), (2025, 2)),
        (date(2026, 4, 1), (2026, 1)),
        (date(2026, 9, 30), (2026, 1)),
        (date(2026, 10, 1), (2026, 2)),
    ],
)
def test_current_fiscal_half(today: date, expected: tuple[int, int]) -> None:
    assert current_fiscal_half(today) == expected


def test_selectable_halves_run_until_the_next_half() -> None:
    assert selectable_halves(date(2026, 10, 26)) == (
        (2025, 1),
        (2025, 2),
        (2026, 1),
        (2026, 2),
        (2027, 1),
    )
    assert selectable_halves(date(2025, 4, 1))[-1] == (2025, 2)


def test_half_and_fiscal_year_labels() -> None:
    assert half_label(2026, 1) == "20期 上期"
    assert half_label(2025, 2) == "19期 下期"
    assert fiscal_year_label(2026) == "20期"


def test_half_label_rejects_invalid_half() -> None:
    with pytest.raises(ValueError, match="half"):
        half_label(2026, 3)


def test_half_months() -> None:
    assert half_months(2026, 1) == (
        (2026, 4),
        (2026, 5),
        (2026, 6),
        (2026, 7),
        (2026, 8),
        (2026, 9),
    )
    assert half_months(2026, 2) == (
        (2026, 10),
        (2026, 11),
        (2026, 12),
        (2027, 1),
        (2027, 2),
        (2027, 3),
    )
