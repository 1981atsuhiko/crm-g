"""社員の一覧・詳細の試験（`SCN-020`、`SCN-021`、`TC-FUN-005`〜`007`）。"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.employee import (
    get_employee,
    list_bonuses,
    list_employees,
    list_rate_history,
    list_salary_history,
    list_salary_history_items,
    list_work_periods,
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
    return path


def _insert_employee(  # noqa: PLR0913 試験用のデータ生成関数のため、項目数が多くてもまとめて並べる
    connection: sqlite3.Connection,
    *,
    employee_id: int,
    email: str,
    name: str,
    name_kana: str | None = None,
    enrollment_status: str = "在籍",
    join_date: str | None = "2020-04-01",
    retire_date: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO employee "
        "(id, email, name, name_kana, enrollment_status, join_date, retire_date, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            employee_id,
            email,
            name,
            name_kana,
            enrollment_status,
            join_date,
            retire_date,
            _TIMESTAMP,
            _TIMESTAMP,
        ),
    )
    connection.commit()


def test_tc_fun_005_default_filter_shows_active_and_leaving_only(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, employee_id=1, email="a@example.com", name="在籍太郎")
        _insert_employee(
            connection,
            employee_id=2,
            email="b@example.com",
            name="退職予定次郎",
            enrollment_status="退職予定",
        )
        _insert_employee(
            connection,
            employee_id=3,
            email="c@example.com",
            name="退職三郎",
            enrollment_status="退職",
            retire_date="2026-03-31",
        )

        default_result = list_employees(connection)
        assert {e.id for e in default_result} == {1, 2}

        with_retired = list_employees(connection, ("在籍", "退職予定", "退職"))
        assert {e.id for e in with_retired} == {1, 2, 3}


def test_tc_fun_006_summary_excludes_sensitive_fields(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, employee_id=1, email="a@example.com", name="在籍太郎")

        summaries = list_employees(connection)
        assert len(summaries) == 1
        # EmployeeSummaryのdataclassは、生年月日・性別・居住エリアのフィールドを持たない。
        fields = summaries[0].__dataclass_fields__
        assert "birth_date" not in fields
        assert "gender" not in fields
        assert "residence_area" not in fields


def test_list_employees_sorted_by_kana_then_name(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(
            connection, employee_id=1, email="a@example.com", name="佐藤", name_kana="サトウ"
        )
        _insert_employee(
            connection, employee_id=2, email="b@example.com", name="鈴木", name_kana="スズキ"
        )
        _insert_employee(connection, employee_id=3, email="c@example.com", name="田中")

        summaries = list_employees(connection)
        assert [e.name for e in summaries] == ["佐藤", "鈴木", "田中"]


def test_list_employees_search_matches_name_or_email(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, employee_id=1, email="taro@example.com", name="山田太郎")
        _insert_employee(connection, employee_id=2, email="jiro@example.com", name="鈴木次郎")

        by_name = list_employees(connection, search="山田")
        assert {e.id for e in by_name} == {1}

        by_email = list_employees(connection, search="jiro")
        assert {e.id for e in by_email} == {2}


def test_list_employees_with_empty_enrollment_statuses_returns_empty(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, employee_id=1, email="a@example.com", name="在籍太郎")

        assert list_employees(connection, ()) == ()


def test_get_employee_returns_none_when_not_found(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assert get_employee(connection, 999) is None


def test_get_employee_returns_full_fields(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, employee_id=1, email="a@example.com", name="在籍太郎")

        employee = get_employee(connection, 1)
        assert employee is not None
        assert employee.email == "a@example.com"
        assert employee.join_date == date(2020, 4, 1)


def test_tc_fun_007_salary_history_ordered_with_monthly_salary_total(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, employee_id=1, email="a@example.com", name="在籍太郎")

        connection.execute(
            "INSERT INTO salary_history "
            "(id, employee_id, effective_from, effective_to, pay_system, employment_type, "
            "revision_type, created_at, updated_at) "
            "VALUES (1, 1, '2025-04-01', '2025-09-30', '通常評価制度', '正社員', '入社', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO salary_history "
            "(id, employee_id, effective_from, pay_system, employment_type, "
            "revision_type, created_at, updated_at) "
            "VALUES (2, 1, '2025-10-01', '通常評価制度', '正社員', '定期（10月）', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO salary_history_item "
            "(salary_history_id, pay_item_code, amount, created_at, updated_at) "
            "VALUES (1, 'BASE_SALARY', 300000, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO salary_history_item "
            "(salary_history_id, pay_item_code, amount, created_at, updated_at) "
            "VALUES (1, 'DUTY_ALLOWANCE', 20000, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        history = list_salary_history(connection, 1)
        assert [row.id for row in history] == [1, 2]
        assert history[0].monthly_salary == 320000
        assert history[1].monthly_salary == 0

        items = list_salary_history_items(connection, 1)
        assert [item.pay_item_code for item in items] == ["BASE_SALARY", "DUTY_ALLOWANCE"]


def test_list_rate_history_joins_assignment(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, employee_id=1, email="a@example.com", name="在籍太郎")
        connection.execute(
            "INSERT INTO assignment "
            "(id, employee_id, customer_name, project_name, category, start_date, "
            "created_at, updated_at) "
            "VALUES (1, 1, '顧客A', '案件A', 'SES', '2025-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO rate_history "
            "(employee_id, assignment_id, effective_from, unit_price, created_at, updated_at) "
            "VALUES (1, 1, '2025-04-01', 800000, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        rates = list_rate_history(connection, 1)
        assert len(rates) == 1
        assert rates[0].customer_name == "顧客A"
        assert rates[0].project_name == "案件A"
        assert rates[0].unit_price == 800000


def test_list_work_periods_joins_work_status_and_assignment(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, employee_id=1, email="a@example.com", name="在籍太郎")
        connection.execute(
            "INSERT INTO work_period "
            "(employee_id, start_date, work_status_code, created_at, updated_at) "
            "VALUES (1, '2025-04-01', 'DEV', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        periods = list_work_periods(connection, 1)
        assert len(periods) == 1
        assert periods[0].work_status_name == "開発稼働"
        assert periods[0].customer_name is None


def test_list_bonuses_ordered_by_half(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, employee_id=1, email="a@example.com", name="在籍太郎")
        connection.execute(
            "INSERT INTO bonus "
            "(employee_id, eval_half, pay_month, base_type, base_amount, eval_rank, "
            "rate_bp, bonus_amount, source, created_at, updated_at) "
            "VALUES (1, '2026-H1', '2026-10', '基本給', 300000, 'A', 15000, 450000, "
            "'アプリ計算', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        bonuses = list_bonuses(connection, 1)
        assert len(bonuses) == 1
        assert bonuses[0].eval_half == "2026-H1"
        assert bonuses[0].bonus_amount == 450000
        assert bonuses[0].is_closed is False
