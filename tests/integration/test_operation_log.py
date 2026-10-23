"""操作ログの書き込みの試験（SCD-020〜024）。"""

import sqlite3
from pathlib import Path

import pytest

from crm_g.application.operation_log import Operation, OperationLogDetail, record_operation_log
from crm_g.infrastructure.db import open_connection, transaction
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def test_record_operation_log_inserts_a_row(db_path: Path) -> None:
    with open_connection(db_path) as connection, transaction(connection):
        record_operation_log(connection, Operation.VIEW, target_table="salary_history")

        row = connection.execute(
            "SELECT operation, target_table, target_key, before_value, after_value "
            "FROM operation_log"
        ).fetchone()
        assert row == ("閲覧", "salary_history", None, None, None)


def test_record_operation_log_serializes_before_after_as_json(db_path: Path) -> None:
    with open_connection(db_path) as connection, transaction(connection):
        record_operation_log(
            connection,
            Operation.UPDATE,
            target_table="salary_history",
            detail=OperationLogDetail(
                target_key="1",
                before_value={"base_salary": 300000},
                after_value={"base_salary": 320000},
            ),
        )

        row = connection.execute("SELECT before_value, after_value FROM operation_log").fetchone()
        assert row[0] == '{"base_salary": 300000}'
        assert row[1] == '{"base_salary": 320000}'


def test_record_operation_log_rolls_back_with_the_caller_transaction(db_path: Path) -> None:
    """同じトランザクション内の他の失敗で、操作ログの記録もロールバックされることを確認する。"""
    with open_connection(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError), transaction(connection):
            record_operation_log(connection, Operation.VIEW, target_table="salary_history")
            # STRICT制約に違反する更新で、トランザクション全体を失敗させる。
            connection.execute(
                "INSERT INTO operation_log (occurred_at, operation, target_table) "
                "VALUES ('invalid', '閲覧', 'x')"
            )

        count = connection.execute("SELECT COUNT(*) FROM operation_log").fetchone()[0]
        assert count == 0
