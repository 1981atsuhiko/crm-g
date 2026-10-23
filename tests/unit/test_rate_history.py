"""単価の決定の試験（CLC-013）。"""

from datetime import date

import pytest

from crm_g.domain.rate_history import RateHistoryEntry, determine_unit_price


def test_determine_unit_price_uses_assignment_specific_history() -> None:
    histories = (
        RateHistoryEntry(assignment_id=1, effective_from=date(2026, 10, 1), unit_price=800_000),
        RateHistoryEntry(assignment_id=None, effective_from=date(2026, 10, 1), unit_price=700_000),
    )

    result = determine_unit_price(date(2026, 10, 10), assignment_id=1, histories=histories)

    assert result == 800_000


def test_determine_unit_price_uses_employee_level_history_when_no_assignment() -> None:
    histories = (
        RateHistoryEntry(assignment_id=None, effective_from=date(2026, 10, 1), unit_price=700_000),
    )

    result = determine_unit_price(date(2026, 10, 10), assignment_id=None, histories=histories)

    assert result == 700_000


def test_determine_unit_price_picks_latest_effective_from() -> None:
    histories = (
        RateHistoryEntry(assignment_id=1, effective_from=date(2026, 10, 1), unit_price=800_000),
        RateHistoryEntry(assignment_id=1, effective_from=date(2026, 10, 15), unit_price=850_000),
    )

    result = determine_unit_price(date(2026, 10, 20), assignment_id=1, histories=histories)

    assert result == 850_000


def test_determine_unit_price_ignores_future_history() -> None:
    histories = (
        RateHistoryEntry(assignment_id=1, effective_from=date(2026, 10, 15), unit_price=850_000),
    )

    result = determine_unit_price(date(2026, 10, 10), assignment_id=1, histories=histories)

    assert result is None


def test_determine_unit_price_returns_none_when_no_history() -> None:
    result = determine_unit_price(date(2026, 10, 10), assignment_id=1, histories=())

    assert result is None


def test_rate_history_rejects_negative_unit_price() -> None:
    with pytest.raises(ValueError, match="unit_price"):
        RateHistoryEntry(assignment_id=None, effective_from=date(2026, 10, 1), unit_price=-1)


def test_determine_unit_price_does_not_fall_back_to_employee_level_history() -> None:
    """稼働期間にassignment_idがある場合、同じ社員のNULLの履歴にはフォールバックしない（CLC-013）。"""
    histories = (
        RateHistoryEntry(assignment_id=None, effective_from=date(2026, 10, 1), unit_price=700_000),
    )

    result = determine_unit_price(date(2026, 10, 10), assignment_id=1, histories=histories)

    assert result is None
