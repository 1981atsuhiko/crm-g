"""祝日の取得の試験（`ARC-012`）。

実物の`jpholiday`を使うのは、`test_year_holidays_includes_new_years_day`だけに
する（テスト計画書の方針。期待値は、2026-01-01（元日）が祝日に含まれることだけ）。
他の試験は、`jpholiday`を固定値に差し替えて、変換処理だけを確認する。
"""

from datetime import date

import jpholiday
import pytest

from crm_g.infrastructure.holidays import year_holidays


def test_year_holidays_includes_new_years_day() -> None:
    assert date(2026, 1, 1) in year_holidays(2026)


def test_year_holidays_returns_only_the_dates_from_jpholiday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_holidays = [(date(2026, 1, 1), "元日"), (date(2026, 2, 11), "建国記念の日")]
    monkeypatch.setattr(jpholiday, "year_holidays", lambda year: fake_holidays)

    assert year_holidays(2026) == frozenset({date(2026, 1, 1), date(2026, 2, 11)})
