"""賞与ルールの試験（`SCN-064`、`bonus_rule`）。"""

from datetime import date
from pathlib import Path

import pytest

from crm_g.application.bonus_rule import (
    InvalidBaseTypeError,
    InvalidValidFromError,
    PeriodOverlapError,
    add_bonus_rule,
    list_bonus_rules,
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


def test_list_bonus_rules_returns_seed_data(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        rules = list_bonus_rules(connection)

        assert len(rules) == 2
        assert rules[0].valid_from == date(2025, 4, 1)
        assert rules[0].valid_to == date(2026, 3, 31)
        assert rules[0].base_type == "基本給"
        assert rules[1].valid_from == date(2026, 4, 1)
        assert rules[1].valid_to is None
        assert rules[1].base_type == "月給合計"


def test_add_bonus_rule_closes_previous_rule(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        add_bonus_rule(connection, date(2027, 4, 1), "基本給")

        rules = list_bonus_rules(connection)
        assert len(rules) == 3
        assert rules[1].valid_to == date(2027, 3, 31)
        assert rules[2].valid_from == date(2027, 4, 1)
        assert rules[2].valid_to is None
        assert rules[2].base_type == "基本給"


def test_add_bonus_rule_rejects_undefined_base_type(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidBaseTypeError):
        add_bonus_rule(connection, date(2027, 4, 1), "未定義の種類")


def test_add_bonus_rule_rejects_valid_from_not_after_current(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidValidFromError):
        add_bonus_rule(connection, date(2026, 4, 1), "基本給")


def test_add_bonus_rule_is_scoped_per_target_pay_system(db_path: Path) -> None:
    """`target_pay_system`が異なれば、独立した期間として扱われる（重ならない）。"""
    with open_connection(db_path) as connection:
        add_bonus_rule(connection, date(2025, 4, 1), "基本給", target_pay_system="年俸制")

        normal_rules = list_bonus_rules(connection, target_pay_system="通常評価制度")
        yearly_rules = list_bonus_rules(connection, target_pay_system="年俸制")
        assert len(normal_rules) == 2
        assert len(yearly_rules) == 1


def test_overlap_is_rejected_when_an_extra_open_period_exists(db_path: Path) -> None:
    """`add_bonus_rule`の重なり検査そのものを確かめる（異常データを直接作る）。"""
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO bonus_rule "
            "(valid_from, valid_to, base_type, target_pay_system, created_at, updated_at) "
            "VALUES ('2026-06-01', NULL, '基本給', '通常評価制度', "
            "'2026-10-24T00:00:00.000000Z', '2026-10-24T00:00:00.000000Z')"
        )
        connection.commit()

        with pytest.raises(PeriodOverlapError):
            add_bonus_rule(connection, date(2026, 7, 1), "月給合計")


def test_add_bonus_rule_failure_rolls_back(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        with pytest.raises(InvalidBaseTypeError):
            add_bonus_rule(connection, date(2027, 4, 1), "未定義の種類")

        rules = list_bonus_rules(connection)
        assert len(rules) == 2
