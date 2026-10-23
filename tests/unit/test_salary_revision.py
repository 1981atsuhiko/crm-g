"""昇給額・改定の登録・改定月の警告の試験（TC-CLC-013〜015）。"""

from datetime import date

import pytest

from crm_g.domain.pay_item import PayItem
from crm_g.domain.salary_history import SalaryHistory, SalaryHistoryItem
from crm_g.domain.salary_revision import (
    calculate_raise_amount,
    determine_previous_effective_to,
    needs_revision_warning,
)

_BASE = PayItem("BASE", "基本給", "基本給", True, False, None, 1)


def _history(amount: int) -> SalaryHistory:
    return SalaryHistory(date(2026, 4, 1), None, (SalaryHistoryItem(_BASE, amount),))


def test_tc_clc_013_raise_amount_and_previous_effective_to() -> None:
    """計算仕様書9.11（EX-010）。"""
    previous = _history(300_000)
    current = _history(320_000)

    assert calculate_raise_amount(current, previous) == 20_000
    assert determine_previous_effective_to(date(2026, 10, 1), date(2026, 4, 1)) == date(2026, 9, 30)


def test_raise_amount_is_none_for_first_history() -> None:
    assert calculate_raise_amount(_history(300_000), None) is None


def test_determine_previous_effective_to_rejects_non_chronological_dates() -> None:
    with pytest.raises(ValueError, match="effective_from"):
        determine_previous_effective_to(date(2026, 4, 1), date(2026, 4, 1))


@pytest.mark.parametrize(
    ("pay_system", "revision_type", "month"),
    [
        ("通常評価制度", "定期（4月）", 5),
        ("年俸制", "年俸改定（4月）", 10),
    ],
)
def test_tc_clc_014_revision_month_warning_present(
    pay_system: str, revision_type: str, month: int
) -> None:
    assert needs_revision_warning(pay_system, revision_type, month) is True


@pytest.mark.parametrize(
    ("pay_system", "revision_type", "month"),
    [
        ("通常評価制度", "定期（4月）", 4),
        ("通常評価制度", "定期（10月）", 10),
        ("年俸制", "年俸改定（4月）", 4),
    ],
)
def test_tc_clc_015_revision_month_warning_absent(
    pay_system: str, revision_type: str, month: int
) -> None:
    assert needs_revision_warning(pay_system, revision_type, month) is False


@pytest.mark.parametrize("revision_type", ["入社", "制度移行"])
def test_revision_warning_absent_for_onboarding_and_system_change(revision_type: str) -> None:
    assert needs_revision_warning("通常評価制度", revision_type, 5) is False


def test_revision_warning_absent_for_rate_linked_pay_system() -> None:
    assert needs_revision_warning("単価連動", "その他", 5) is False


def test_revision_warning_present_for_other_revision_type_outside_target_month() -> None:
    """改定区分「その他」は、入社・制度移行と異なり、警告の対象にする（12章未決事項#11）。"""
    assert needs_revision_warning("通常評価制度", "その他", 5) is True


def test_revision_warning_absent_for_other_revision_type_in_target_month() -> None:
    assert needs_revision_warning("通常評価制度", "その他", 4) is False


def test_needs_revision_warning_rejects_month_out_of_range() -> None:
    with pytest.raises(ValueError, match="effective_from_month"):
        needs_revision_warning("通常評価制度", "その他", 13)
