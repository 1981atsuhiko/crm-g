"""有効期間の重なり判定の試験（データ設計書10章-9、`TC-DBD-016`）。"""

from datetime import date

import pytest

from crm_g.domain.period_overlap import periods_overlap


def _d(value: str) -> date:
    return date.fromisoformat(value)


@pytest.mark.parametrize(
    ("start_a", "end_a", "start_b", "end_b", "expected"),
    [
        # 完全に同じ期間。
        ("2025-04-01", "2025-09-30", "2025-04-01", "2025-09-30", True),
        # 一方がもう一方を含む。
        ("2025-04-01", None, "2025-06-01", "2025-06-30", True),
        # 一部が重なる。
        ("2025-04-01", "2025-09-30", "2025-07-01", "2026-03-31", True),
        # 隣接（同じ日に切り替わる）。重なりとして扱う。
        ("2025-04-01", "2025-09-30", "2025-09-30", "2026-03-31", True),
        # 隣接（前日で終わり、翌日から始まる）。重ならない。
        ("2025-04-01", "2025-09-30", "2025-10-01", "2026-03-31", False),
        # 両方とも継続中（終了日なし）。
        ("2025-04-01", None, "2026-04-01", None, True),
        # 片方が継続中で、もう一方がその前に完全に終わっている。
        ("2025-04-01", "2025-09-30", "2026-04-01", None, False),
        # 完全に離れている。
        ("2025-04-01", "2025-09-30", "2026-04-01", "2026-09-30", False),
    ],
)
def test_periods_overlap(
    start_a: str, end_a: str | None, start_b: str, end_b: str | None, expected: bool
) -> None:
    assert (
        periods_overlap(
            _d(start_a),
            _d(end_a) if end_a else None,
            _d(start_b),
            _d(end_b) if end_b else None,
        )
        is expected
    )


def test_periods_overlap_is_symmetric() -> None:
    start_a, end_a = _d("2025-04-01"), _d("2025-09-30")
    start_b, end_b = _d("2025-07-01"), None

    assert periods_overlap(start_a, end_a, start_b, end_b) == periods_overlap(
        start_b, end_b, start_a, end_a
    )
