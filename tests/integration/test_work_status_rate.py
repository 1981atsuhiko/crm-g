"""稼働状態の給与支給率の試験（`SCN-061`、`TC-FUN-015`、`TC-SCN-016`、`TC-DBD-016`）。"""

from datetime import date
from pathlib import Path

import pytest

from crm_g.application.work_status_rate import (
    InvalidRateError,
    InvalidValidFromError,
    PeriodOverlapError,
    change_rate,
    list_work_statuses,
    rate_history,
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


def test_list_work_statuses_includes_seed_data_in_sort_order(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        statuses = list_work_statuses(connection)

        assert [status.code for status in statuses][:3] == ["DEV", "EDU", "OTH"]
        assert statuses[0].current_rate_bp == 10000
        assert statuses[0].is_billable is True


def test_change_rate_for_a_status_with_no_existing_rate(db_path: Path) -> None:
    """支給率の行が1件もない稼働状態に、最初の期間を登録する（current is Noneの経路）。"""
    with open_connection(db_path) as connection:
        connection.execute("DELETE FROM work_status_rate WHERE work_status_code = 'DEV'")
        connection.commit()

        change_rate(connection, "DEV", "100", date(2025, 4, 1))

        history = rate_history(connection, "DEV")
        assert len(history) == 1
        assert history[0].valid_from == date(2025, 4, 1)
        assert history[0].valid_to is None
        assert history[0].payroll_rate_bp == 10000


def test_rate_history_returns_seed_period(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        history = rate_history(connection, "DEV")

        assert len(history) == 1
        assert history[0].valid_from == date(2025, 4, 1)
        assert history[0].valid_to is None
        assert history[0].payroll_rate_bp == 10000


def test_tc_fun_015_change_rate_keeps_past_period_and_uses_new_one(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        change_rate(connection, "DEV", "85.50", date(2026, 4, 1))

        history = rate_history(connection, "DEV")
        assert len(history) == 2

        past, current = history
        assert past.valid_from == date(2025, 4, 1)
        assert past.valid_to == date(2026, 3, 31)
        assert past.payroll_rate_bp == 10000

        assert current.valid_from == date(2026, 4, 1)
        assert current.valid_to is None
        assert current.payroll_rate_bp == 8550


def test_tc_scn_016_rate_above_100_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidRateError):
        change_rate(connection, "DEV", "100.01", date(2026, 4, 1))


def test_rate_exactly_100_is_accepted(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        change_rate(connection, "DEV", "100", date(2026, 4, 1))

        history = rate_history(connection, "DEV")
        assert history[-1].payroll_rate_bp == 10000


def test_rate_with_more_than_two_decimal_places_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidRateError):
        change_rate(connection, "DEV", "85.555", date(2026, 4, 1))


def test_rate_non_numeric_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidRateError):
        change_rate(connection, "DEV", "abc", date(2026, 4, 1))


def test_rate_nan_is_rejected(db_path: Path) -> None:
    """`Decimal("nan")` は構築時に例外にならないため、別途弾く必要がある。"""
    with open_connection(db_path) as connection, pytest.raises(InvalidRateError):
        change_rate(connection, "DEV", "nan", date(2026, 4, 1))


def test_rate_negative_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidRateError):
        change_rate(connection, "DEV", "-1", date(2026, 4, 1))


def test_valid_from_equal_to_current_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidValidFromError):
        change_rate(connection, "DEV", "90", date(2025, 4, 1))


def test_valid_from_before_current_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidValidFromError):
        change_rate(connection, "DEV", "90", date(2025, 1, 1))


def test_valid_from_before_current_is_rejected_by_valid_from_check_not_overlap(
    db_path: Path,
) -> None:
    """`change_rate`は唯一の書き込み経路のため、過去日付の指定は常にInvalidValidFromError

    として先に拒否される（現在の支給率の適用開始日より後であることのチェックが、
    重なりのチェックより先に働く）。既存の過去期間と重なる開始日を指定しても、
    この経路からは`PeriodOverlapError`に到達しない。
    """
    with open_connection(db_path) as connection:
        change_rate(connection, "DEV", "85", date(2026, 4, 1))

        with pytest.raises(InvalidValidFromError):
            change_rate(connection, "DEV", "50", date(2026, 1, 1))


def test_tc_dbd_016_overlapping_period_is_rejected(db_path: Path) -> None:
    """`change_rate`の重なり検査そのものを確かめる（データ設計書10章-9）。

    `change_rate`は常に単一のオープン期間という不変条件を保つため、通常の操作
    からは重なりを作れない。ここでは、異常なデータ（オープン期間が2行ある状態）
    をDBへ直接作ってから`change_rate`を呼び、重なり検査が実際に機能することを
    確認する。
    """
    with open_connection(db_path) as connection:
        # 通常はあり得ない状態（オープン期間が重複して2行ある）を、直接作る。
        connection.execute(
            "INSERT INTO work_status_rate "
            "(work_status_code, valid_from, valid_to, payroll_rate_bp, created_at, updated_at) "
            "VALUES ('DEV', '2026-06-01', NULL, 9000, "
            "'2026-10-24T00:00:00.000000Z', '2026-10-24T00:00:00.000000Z')"
        )
        connection.commit()

        # 既存の2つのオープン期間（2025-04-01〜、2026-06-01〜）より後の開始日を
        # 指定しても、2026-06-01〜の期間（調整対象ではない、もう一方のオープン行）
        # と重なるため、拒否される。
        with pytest.raises(PeriodOverlapError):
            change_rate(connection, "DEV", "50", date(2026, 7, 1))


def test_change_rate_does_not_affect_other_work_status(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        change_rate(connection, "DEV", "85", date(2026, 4, 1))

        other_history = rate_history(connection, "EDU")
        assert len(other_history) == 1
        assert other_history[0].payroll_rate_bp == 10000


def test_change_rate_failure_rolls_back_on_invalid_rate(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        with pytest.raises(InvalidRateError):
            change_rate(connection, "DEV", "100.01", date(2026, 4, 1))

        history = rate_history(connection, "DEV")
        assert len(history) == 1
