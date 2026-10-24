"""計算設定の試験（`SCN-063`、`system_setting`）。"""

from datetime import date
from pathlib import Path

import pytest

from crm_g.application.system_setting import (
    InvalidCoefficientError,
    InvalidValidFromError,
    PeriodOverlapError,
    change_coefficient,
    coefficient_history,
    current_rounding,
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


def test_coefficient_history_returns_seed_period(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        history = coefficient_history(connection)

        assert len(history) == 1
        assert history[0].valid_from == date(2025, 4, 1)
        assert history[0].valid_to is None
        assert history[0].coefficient_bp == 12000


def test_current_rounding_is_round_up(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assert current_rounding(connection) == "切り上げ"


def test_change_coefficient_keeps_past_period_and_uses_new_one(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        change_coefficient(connection, "1.5", date(2026, 4, 1))

        history = coefficient_history(connection)
        assert len(history) == 2

        past, current = history
        assert past.valid_to == date(2026, 3, 31)
        assert past.coefficient_bp == 12000
        assert current.valid_from == date(2026, 4, 1)
        assert current.valid_to is None
        assert current.coefficient_bp == 15000


def test_coefficient_with_four_decimal_places_is_accepted(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        change_coefficient(connection, "1.2345", date(2026, 4, 1))

        history = coefficient_history(connection)
        assert history[-1].coefficient_bp == 12345


def test_coefficient_with_more_than_four_decimal_places_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidCoefficientError):
        change_coefficient(connection, "1.23456", date(2026, 4, 1))


def test_coefficient_zero_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidCoefficientError):
        change_coefficient(connection, "0", date(2026, 4, 1))


def test_coefficient_negative_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidCoefficientError):
        change_coefficient(connection, "-1", date(2026, 4, 1))


def test_coefficient_non_numeric_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidCoefficientError):
        change_coefficient(connection, "abc", date(2026, 4, 1))


def test_coefficient_nan_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidCoefficientError):
        change_coefficient(connection, "nan", date(2026, 4, 1))


def test_coefficient_infinity_is_rejected(db_path: Path) -> None:
    """`Decimal("Infinity")`は0より大きいため、infが未捕捉のOverflowErrorにならないか確認する。"""
    with open_connection(db_path) as connection, pytest.raises(InvalidCoefficientError):
        change_coefficient(connection, "Infinity", date(2026, 4, 1))


def test_valid_from_not_after_current_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidValidFromError):
        change_coefficient(connection, "1.5", date(2025, 4, 1))


def test_overlap_is_rejected_when_an_extra_open_period_exists(db_path: Path) -> None:
    """`change_coefficient`の重なり検査そのものを確かめる（異常データを直接作る）。"""
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO system_setting "
            "(setting_key, valid_from, valid_to, value_integer, created_at, updated_at) "
            "VALUES ('原価係数', '2026-06-01', NULL, 13000, "
            "'2026-10-24T00:00:00.000000Z', '2026-10-24T00:00:00.000000Z')"
        )
        connection.commit()

        with pytest.raises(PeriodOverlapError):
            change_coefficient(connection, "1.4", date(2026, 7, 1))


def test_change_coefficient_failure_rolls_back(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        with pytest.raises(InvalidCoefficientError):
            change_coefficient(connection, "abc", date(2026, 4, 1))

        history = coefficient_history(connection)
        assert len(history) == 1
