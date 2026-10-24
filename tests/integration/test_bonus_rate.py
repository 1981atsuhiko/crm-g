"""評価ランクの掛け率の試験（`SCN-064`、`bonus_rate`）。"""

from pathlib import Path

import pytest

from crm_g.application.bonus_rate import (
    EVAL_RANKS,
    InvalidEvalHalfError,
    InvalidRateError,
    list_rates,
    save_rates,
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


def test_list_rates_is_empty_before_registration(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        rates = list_rates(connection, "2026-H1")

        assert [rate.eval_rank for rate in rates] == list(EVAL_RANKS)
        assert all(rate.rate_bp is None for rate in rates)


def test_save_rates_registers_and_reads_back(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        save_rates(
            connection,
            "2026-H1",
            {"S": "2.0000", "A": "1.5", "B": "1", "C": "0.5", "D": "0"},
        )

        rates = list_rates(connection, "2026-H1")
        by_rank = {rate.eval_rank: rate.rate_bp for rate in rates}
        assert by_rank == {"S": 20000, "A": 15000, "B": 10000, "C": 5000, "D": 0}


def test_save_rates_blank_value_means_unregistered(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        save_rates(connection, "2026-H1", {"S": "2.0"})
        save_rates(connection, "2026-H1", {"S": ""})

        rates = list_rates(connection, "2026-H1")
        s_rate = next(rate for rate in rates if rate.eval_rank == "S")
        assert s_rate.rate_bp is None


def test_save_rates_overwrites_existing_value(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        save_rates(connection, "2026-H1", {"S": "2.0"})
        save_rates(connection, "2026-H1", {"S": "3.0"})

        rates = list_rates(connection, "2026-H1")
        s_rate = next(rate for rate in rates if rate.eval_rank == "S")
        assert s_rate.rate_bp == 30000


def test_save_rates_is_scoped_per_half(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        save_rates(connection, "2026-H1", {"S": "2.0"})

        h2_rates = list_rates(connection, "2026-H2")
        assert all(rate.rate_bp is None for rate in h2_rates)


def test_save_rates_rejects_invalid_eval_half_format(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidEvalHalfError):
        save_rates(connection, "2026年上期", {"S": "1.0"})


def test_save_rates_rejects_negative_rate(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidRateError):
        save_rates(connection, "2026-H1", {"S": "-1"})


def test_save_rates_rejects_more_than_four_decimal_places(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidRateError):
        save_rates(connection, "2026-H1", {"S": "1.23456"})


def test_save_rates_rejects_non_numeric(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidRateError):
        save_rates(connection, "2026-H1", {"S": "abc"})


def test_save_rates_rejects_nan(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidRateError):
        save_rates(connection, "2026-H1", {"S": "nan"})


def test_save_rates_accepts_zero(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        save_rates(connection, "2026-H1", {"D": "0"})

        rates = list_rates(connection, "2026-H1")
        d_rate = next(rate for rate in rates if rate.eval_rank == "D")
        assert d_rate.rate_bp == 0


def test_save_rates_has_no_upper_bound(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        save_rates(connection, "2026-H1", {"S": "999"})

        rates = list_rates(connection, "2026-H1")
        s_rate = next(rate for rate in rates if rate.eval_rank == "S")
        assert s_rate.rate_bp == 9990000


def test_save_rates_failure_rolls_back_entire_batch(db_path: Path) -> None:
    """1つのランクが不正なら、他のランクも保存されない（半期ごとにまとめて保存）。"""
    with open_connection(db_path) as connection:
        with pytest.raises(InvalidRateError):
            save_rates(connection, "2026-H1", {"S": "1.0", "A": "abc"})

        rates = list_rates(connection, "2026-H1")
        assert all(rate.rate_bp is None for rate in rates)
