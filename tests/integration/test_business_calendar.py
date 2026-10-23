"""営業日カレンダーの試験（`SCN-060`、`TC-ARC-019`、`TC-DBD-016`）。

`TC-ARC-019` だけは、実物の `jpholiday` を使う（テスト計画書3章の方針）。期待値は、
2026-01-01（元日）が祝日になることだけにする。他の試験は、`jpholiday` を介さない
確認（土日の判定、手動登録の保護など）にとどめる。
"""

from datetime import date
from pathlib import Path

import pytest

import crm_g.application.business_calendar as business_calendar_module
from crm_g.application.business_calendar import (
    CalendarAlreadyExistsError,
    create_year_calendar,
    list_month,
    month_business_day_count,
    refetch_holidays,
    register_manual_day,
    year_exists,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def test_year_exists_is_false_before_creation(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assert year_exists(connection, 2026) is False


def test_tc_arc_019_create_year_calendar_marks_new_years_day_as_holiday(db_path: Path) -> None:
    """実物のjpholidayを使う（テスト計画書の方針）。期待値は元日が祝日になることだけ。"""
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        row = connection.execute(
            "SELECT is_business_day, source FROM business_calendar WHERE calendar_date = ?",
            ("2026-01-01",),
        ).fetchone()
        assert row == (0, "自動取込")


def test_create_year_calendar_marks_weekends_as_non_business(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        # 2026-01-03は土曜日。
        row = connection.execute(
            "SELECT is_business_day FROM business_calendar WHERE calendar_date = ?",
            ("2026-01-03",),
        ).fetchone()
        assert row[0] == 0


def test_create_year_calendar_marks_ordinary_weekday_as_business(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # jpholidayの実データに依存しないよう、祝日なしとして扱う（テスト計画書の方針）。
    monkeypatch.setattr(business_calendar_module, "year_holidays", lambda year: frozenset())

    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        # 2026-01-05は月曜日。
        row = connection.execute(
            "SELECT is_business_day FROM business_calendar WHERE calendar_date = ?",
            ("2026-01-05",),
        ).fetchone()
        assert row[0] == 1


def test_create_year_calendar_inserts_all_days_of_the_year(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        count = connection.execute("SELECT COUNT(*) FROM business_calendar").fetchone()[0]
        assert count == 365


def test_create_year_calendar_inserts_all_days_of_a_leap_year(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2028)

        count = connection.execute("SELECT COUNT(*) FROM business_calendar").fetchone()[0]
        assert count == 366

        row = connection.execute(
            "SELECT 1 FROM business_calendar WHERE calendar_date = '2028-02-29'"
        ).fetchone()
        assert row is not None


def test_create_year_calendar_rejects_when_year_already_exists(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        with pytest.raises(CalendarAlreadyExistsError):
            create_year_calendar(connection, 2026)


def test_refetch_holidays_does_not_overwrite_manual_entry(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        # 2026-01-05（月曜・平日）を、会社休日として手動登録する。
        register_manual_day(connection, date(2026, 1, 5), is_business_day=False, note="会社休日")

        refetch_holidays(connection, 2026)

        row = connection.execute(
            "SELECT is_business_day, source FROM business_calendar WHERE calendar_date = ?",
            ("2026-01-05",),
        ).fetchone()
        assert row == (0, "手動登録")


def test_refetch_holidays_updates_automatic_entries(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # jpholidayの実データに依存しないよう、祝日の定義が変わった状況を固定値で模す。
    fixed_holiday = date(2026, 1, 5)

    with open_connection(db_path) as connection:
        # 作成時点では、祝日なしとして扱う（2026-01-05は月曜・平日のため営業日になる）。
        monkeypatch.setattr(business_calendar_module, "year_holidays", lambda year: frozenset())
        create_year_calendar(connection, 2026)

        row_before = connection.execute(
            "SELECT is_business_day FROM business_calendar WHERE calendar_date = ?",
            (fixed_holiday.isoformat(),),
        ).fetchone()
        assert row_before[0] == 1

        # 祝日の定義が変わり、2026-01-05が新たに祝日になった状況で再取込する。
        monkeypatch.setattr(
            business_calendar_module, "year_holidays", lambda year: frozenset({fixed_holiday})
        )
        refetch_holidays(connection, 2026)

        row_after = connection.execute(
            "SELECT is_business_day, source FROM business_calendar WHERE calendar_date = ?",
            (fixed_holiday.isoformat(),),
        ).fetchone()
        assert row_after == (0, "自動取込")


def test_register_manual_day_on_new_date_inserts_a_row(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        # 既存の行がない日（2026年の範囲外）を手動登録する。
        register_manual_day(connection, date(2027, 1, 1), is_business_day=True, note="土曜出勤")

        row = connection.execute(
            "SELECT is_business_day, source, note FROM business_calendar WHERE calendar_date = ?",
            ("2027-01-01",),
        ).fetchone()
        assert row == (1, "手動登録", "土曜出勤")


def test_register_manual_day_overwrites_existing_row(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        register_manual_day(connection, date(2026, 1, 5), is_business_day=False, note="年末年始")

        row = connection.execute(
            "SELECT is_business_day, source, note FROM business_calendar WHERE calendar_date = ?",
            ("2026-01-05",),
        ).fetchone()
        assert row == (0, "手動登録", "年末年始")

        count = connection.execute("SELECT COUNT(*) FROM business_calendar").fetchone()[0]
        assert count == 365


def test_list_month_returns_rows_in_date_order(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        days = list_month(connection, 2026, 1)

        assert len(days) == 31
        assert days[0].calendar_date == date(2026, 1, 1)
        assert days[0].is_business_day is False
        assert days[0].source == "自動取込"
        assert list(days) == sorted(days, key=lambda day: day.calendar_date)


def test_month_business_day_count_matches_business_days(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        days = list_month(connection, 2026, 1)
        expected = sum(1 for day in days if day.is_business_day)

        assert month_business_day_count(connection, 2026, 1) == expected
