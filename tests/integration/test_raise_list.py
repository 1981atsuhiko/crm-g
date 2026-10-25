"""昇給一覧の試験（`SCN-031`、`TC-FUN-010`、`CLC-016`）。"""

from datetime import date
from pathlib import Path

import pytest

from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    list_raises,
    register_salary_history,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-24T00:00:00.000000Z"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) "
            "VALUES "
            "(1, 'taro@example.com', '山田太郎', '在籍', '2020-04-01', ?, ?), "
            "(2, 'hanako@example.com', '鈴木花子', '在籍', '2021-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP, _TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
    return path


def _input(**overrides: object) -> SalaryHistoryInput:
    defaults: dict[str, object] = {
        "effective_from": date(2026, 4, 1),
        "pay_system": "通常評価制度",
        "employment_type": "正社員",
        "grade": None,
        "annual_salary": None,
        "revision_type": "定期（4月）",
        "note": None,
        "items": (SalaryItemInput(pay_item_code="BASE_SALARY", amount=300000),),
    }
    defaults.update(overrides)
    return SalaryHistoryInput(**defaults)  # type: ignore[arg-type]


def test_tc_fun_010_raise_amount_is_difference_from_previous(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        register_salary_history(
            connection,
            1,
            _input(
                effective_from=date(2025, 4, 1),
                items=(SalaryItemInput(pay_item_code="BASE_SALARY", amount=280000),),
            ),
        )
        register_salary_history(
            connection,
            1,
            _input(
                effective_from=date(2026, 4, 1),
                items=(SalaryItemInput(pay_item_code="BASE_SALARY", amount=300000),),
            ),
        )

        rows = list_raises(connection, date(2026, 4, 1), date(2026, 9, 30))

        assert len(rows) == 1
        assert rows[0].employee_name == "山田太郎"
        assert rows[0].previous_monthly_salary == 280000
        assert rows[0].monthly_salary == 300000
        assert rows[0].raise_amount == 20000


def test_tc_fun_010_raise_amount_is_none_without_previous_history(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        register_salary_history(connection, 1, _input(effective_from=date(2026, 4, 1)))

        rows = list_raises(connection, date(2026, 4, 1), date(2026, 9, 30))

        assert len(rows) == 1
        assert rows[0].previous_monthly_salary is None
        assert rows[0].raise_amount is None


def test_tc_fun_010_raise_amount_when_previous_history_has_no_items(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        # 内訳0円の履歴（amount>=0のため許可される）を直前の履歴として登録する。
        register_salary_history(
            connection,
            1,
            _input(
                effective_from=date(2025, 4, 1),
                items=(SalaryItemInput(pay_item_code="BASE_SALARY", amount=0),),
            ),
        )
        register_salary_history(
            connection,
            1,
            _input(
                effective_from=date(2026, 4, 1),
                items=(SalaryItemInput(pay_item_code="BASE_SALARY", amount=300000),),
            ),
        )

        rows = list_raises(connection, date(2026, 4, 1), date(2026, 9, 30))

        assert len(rows) == 1
        # 「直前の履歴が無い」(None)ではなく、「直前の履歴の月給合計が0円」と区別できること。
        assert rows[0].previous_monthly_salary == 0
        assert rows[0].raise_amount == 300000


def test_list_raises_orders_by_effective_from_descending(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        register_salary_history(connection, 1, _input(effective_from=date(2026, 4, 1)))
        register_salary_history(connection, 2, _input(effective_from=date(2026, 5, 1)))

        rows = list_raises(connection, date(2026, 4, 1), date(2026, 9, 30))

        assert [row.effective_from for row in rows] == [date(2026, 5, 1), date(2026, 4, 1)]


def test_list_raises_excludes_entries_outside_period(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        register_salary_history(connection, 1, _input(effective_from=date(2026, 3, 31)))
        register_salary_history(connection, 2, _input(effective_from=date(2026, 10, 1)))

        rows = list_raises(connection, date(2026, 4, 1), date(2026, 9, 30))

        assert rows == ()


def test_list_raises_filters_by_name_search(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        register_salary_history(connection, 1, _input(effective_from=date(2026, 4, 1)))
        register_salary_history(connection, 2, _input(effective_from=date(2026, 4, 1)))

        rows = list_raises(connection, date(2026, 4, 1), date(2026, 9, 30), search="鈴木")

        assert len(rows) == 1
        assert rows[0].employee_name == "鈴木花子"


def test_list_raises_returns_empty_when_no_history(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        rows = list_raises(connection, date(2026, 4, 1), date(2026, 9, 30))

        assert rows == ()
