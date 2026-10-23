"""DDL（給与・賞与テーブル）の試験（TC-DBD-007）。

実際の DDL/ 配下の連番SQLに対して実施する（実装計画書5.3節）。TC-DBD-008
（共通列）は、`test_ddl_master_tables.py` が同じ DDL/ 全体に対して検査して
おり、本PRで追加したテーブルもその対象に含まれるため、ここでは繰り返さない。
"""

import sqlite3
from pathlib import Path

import pytest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-21T00:00:00.000000Z"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def _insert_employee(connection: sqlite3.Connection, employee_id: int) -> None:
    connection.execute(
        "INSERT INTO employee (id, email, name, enrollment_status, created_at, updated_at) "
        "VALUES (?, ?, '山田太郎', '在籍', ?, ?)",
        (employee_id, f"taro{employee_id}@example.com", _TIMESTAMP, _TIMESTAMP),
    )


def test_tc_dbd_007_referenced_employee_cannot_be_deleted(db_path: Path) -> None:
    """他のテーブルから参照されている社員を削除する（TC-DBD-007）。"""
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO salary_history "
            "(employee_id, effective_from, pay_system, employment_type, revision_type, "
            "created_at, updated_at) "
            "VALUES (1, '2026-04-01', '通常評価制度', '正社員', '入社', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM employee WHERE id = 1")


def test_salary_history_rejects_annual_salary_without_annual_pay_system(db_path: Path) -> None:
    """年俸制でないのに annual_salary が設定されている場合を拒否する。"""
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO salary_history "
                "(employee_id, effective_from, pay_system, employment_type, annual_salary, "
                "revision_type, created_at, updated_at) "
                "VALUES (1, '2026-04-01', '通常評価制度', '正社員', 4000000, '入社', ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def test_salary_history_requires_annual_salary_for_annual_pay_system(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO salary_history "
                "(employee_id, effective_from, pay_system, employment_type, "
                "revision_type, created_at, updated_at) "
                "VALUES (1, '2026-04-01', '年俸制', '正社員', '入社', ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def test_monthly_payroll_closed_at_required_when_closed(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO monthly_payroll "
                "(employee_id, year_month, monthly_salary, paid_amount, deduction, "
                "check_result, calculated_at, is_closed, created_at, updated_at) "
                "VALUES (1, '2026-10', 300000, 300000, 0, 'OK', ?, 1, ?, ?)",
                (_TIMESTAMP, _TIMESTAMP, _TIMESTAMP),
            )


def test_bonus_rejects_pay_month_other_than_april_or_october(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bonus "
                "(employee_id, eval_half, pay_month, base_type, base_amount, bonus_amount, "
                "source, created_at, updated_at) "
                "VALUES (1, '2026-H1', '2026-05', '基本給', 300000, 375000, 'アプリ計算', ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def test_bonus_requires_eval_rank_and_rate_when_not_imported(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bonus "
                "(employee_id, eval_half, pay_month, base_type, base_amount, bonus_amount, "
                "source, created_at, updated_at) "
                "VALUES (1, '2026-H1', '2026-10', '基本給', 300000, 375000, 'アプリ計算', ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def test_bonus_accepts_imported_without_eval_rank(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO bonus "
            "(employee_id, eval_half, pay_month, base_type, base_amount, bonus_amount, "
            "source, created_at, updated_at) "
            "VALUES (1, '2026-H1', '2026-10', '基本給', 300000, 375000, '取込', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        row = connection.execute("SELECT bonus_amount FROM bonus WHERE employee_id = 1").fetchone()
        assert row is not None
