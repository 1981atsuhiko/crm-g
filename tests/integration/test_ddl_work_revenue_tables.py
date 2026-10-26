"""DDL（稼働・売上テーブル）の試験。

実際の DDL/ 配下の連番SQLに対して実施する（実装計画書5.3節）。TC-DBD-008
（共通列）は、PH-04-1の試験がDDL/全体に対して検証しているため、ここでは
繰り返さない。TC-DBD-007（外部キーの削除）は、PH-04-2で`salary_history`→
`employee`の1パターンを確認済みだが、本PRで新たに登場する参照パターン
（`work_status`参照、`assignment`参照）は未検証のため、ここで確認する。
"""

import sqlite3
from pathlib import Path

import pytest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import MigrationFailedError, apply_migrations

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


def test_assignment_rejects_end_date_before_start_date(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO assignment "
                "(employee_id, customer_name, category, start_date, end_date, "
                "created_at, updated_at) "
                "VALUES (1, '顧客A', 'SES', '2026-10-01', '2026-09-30', ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def test_work_period_rejects_billing_ratio_above_10000(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO work_status "
            "(code, name, is_billable, is_cost_target, sort_order, created_at, updated_at) "
            "VALUES ('TEST_DEV', '開発稼働', 1, 1, 1, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO work_period "
                "(employee_id, start_date, work_status_code, billing_ratio_bp, "
                "created_at, updated_at) "
                "VALUES (1, '2026-10-01', 'TEST_DEV', 10001, ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def test_work_period_defaults_billing_ratio_to_10000(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO work_status "
            "(code, name, is_billable, is_cost_target, sort_order, created_at, updated_at) "
            "VALUES ('TEST_DEV', '開発稼働', 1, 1, 1, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO work_period (employee_id, start_date, work_status_code, "
            "created_at, updated_at) "
            "VALUES (1, '2026-10-01', 'TEST_DEV', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        row = connection.execute(
            "SELECT billing_ratio_bp FROM work_period WHERE employee_id = 1"
        ).fetchone()
        assert row[0] == 10000


def test_rate_history_rejects_null_assignment(db_path: Path) -> None:
    """単価は、必ず参画に紐づく（DDL/023、データ設計書 DBD-018）。"""
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO rate_history (employee_id, effective_from, unit_price, "
                "created_at, updated_at) "
                "VALUES (1, '2026-10-01', 800000, ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def _copy_ddl_until(target_dir: Path, last_version: int) -> None:
    target_dir.mkdir()
    for source in sorted(_DDL_DIR.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= last_version:
            (target_dir / source.name).write_bytes(source.read_bytes())


def test_migration_023_keeps_existing_rate_history(tmp_path: Path) -> None:
    ddl_dir = tmp_path / "ddl"
    _copy_ddl_until(ddl_dir, 22)
    path = tmp_path / "old.db"
    with open_connection(path) as connection:
        apply_migrations(connection, ddl_dir, path, tmp_path / "backups")
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO assignment "
            "(id, employee_id, customer_name, category, start_date, created_at, updated_at) "
            "VALUES (1, 1, '顧客A', 'SES', '2026-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO rate_history "
            "(id, employee_id, assignment_id, effective_from, unit_price, created_at, updated_at) "
            "VALUES (7, 1, 1, '2026-04-01', 800000, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        (ddl_dir / "023_require_rate_history_assignment.sql").write_bytes(
            (_DDL_DIR / "023_require_rate_history_assignment.sql").read_bytes()
        )
        apply_migrations(connection, ddl_dir, path, tmp_path / "backups")

        row = connection.execute(
            "SELECT id, assignment_id, unit_price FROM rate_history"
        ).fetchone()
        assert row == (7, 1, 800000)


def test_migration_023_fails_when_rate_history_without_assignment_remains(
    tmp_path: Path,
) -> None:
    ddl_dir = tmp_path / "ddl"
    _copy_ddl_until(ddl_dir, 22)
    path = tmp_path / "old.db"
    with open_connection(path) as connection:
        apply_migrations(connection, ddl_dir, path, tmp_path / "backups")
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO rate_history (employee_id, effective_from, unit_price, "
            "created_at, updated_at) VALUES (1, '2026-04-01', 800000, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        (ddl_dir / "023_require_rate_history_assignment.sql").write_bytes(
            (_DDL_DIR / "023_require_rate_history_assignment.sql").read_bytes()
        )
        with pytest.raises(MigrationFailedError):
            apply_migrations(connection, ddl_dir, path, tmp_path / "backups")
        # 失敗したら、開いたままのトランザクションを残さない。
        assert not connection.in_transaction

    # 失敗した作り直しは取り消され、元の表と行がそのまま残る。
    with open_connection(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        count = connection.execute("SELECT COUNT(*) FROM rate_history").fetchone()[0]
    assert "rate_history_new" not in tables
    assert count == 1


def test_forecast_rejects_undefined_confidence(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO forecast (employee_id, year_month, confidence, "
                "created_at, updated_at) "
                "VALUES (1, '2026-10', '不明', ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def test_monthly_pl_rejects_inconsistent_cost_total(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO monthly_pl "
                "(employee_id, year_month, sales, cost_salary, cost_allowance, cost_bonus, "
                "cost_total, gross_profit, source, created_at, updated_at) "
                "VALUES (1, '2026-10', 500000, 300000, 0, 0, 999999, 200000, "
                "'アプリ計算', ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def test_monthly_pl_accepts_consistent_values(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO monthly_pl "
            "(employee_id, year_month, sales, cost_salary, cost_allowance, cost_bonus, "
            "cost_total, gross_profit, source, created_at, updated_at) "
            "VALUES (1, '2026-10', 500000, 300000, 0, 0, 300000, 200000, 'アプリ計算', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        row = connection.execute(
            "SELECT gross_profit FROM monthly_pl WHERE employee_id = 1"
        ).fetchone()
        assert row[0] == 200000


def test_monthly_pl_accepts_negative_gross_profit(db_path: Path) -> None:
    """粗利は負の値を取り得る（CHECKで0以上に制限していないことを確認する）。"""
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO monthly_pl "
            "(employee_id, year_month, sales, cost_salary, cost_allowance, cost_bonus, "
            "cost_total, gross_profit, source, created_at, updated_at) "
            "VALUES (1, '2026-10', 100000, 300000, 0, 0, 300000, -200000, 'アプリ計算', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        row = connection.execute(
            "SELECT gross_profit FROM monthly_pl WHERE employee_id = 1"
        ).fetchone()
        assert row[0] == -200000


def test_monthly_pl_rejects_inconsistent_gross_profit(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO monthly_pl "
                "(employee_id, year_month, sales, cost_salary, cost_allowance, cost_bonus, "
                "cost_total, gross_profit, source, created_at, updated_at) "
                "VALUES (1, '2026-10', 500000, 300000, 0, 0, 300000, 999999, "
                "'アプリ計算', ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def test_rate_history_distinguishes_assignment_id(db_path: Path) -> None:
    """assignment_idが異なれば、同じemployee_id・effective_fromでも重複にならない。"""
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO assignment "
            "(employee_id, customer_name, category, start_date, created_at, updated_at) "
            "VALUES (1, '顧客A', 'SES', '2026-10-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO assignment "
            "(employee_id, customer_name, category, start_date, created_at, updated_at) "
            "VALUES (1, '顧客B', 'SES', '2026-10-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO rate_history "
            "(employee_id, assignment_id, effective_from, unit_price, created_at, updated_at) "
            "VALUES (1, 1, '2026-10-01', 800000, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO rate_history "
            "(employee_id, assignment_id, effective_from, unit_price, created_at, updated_at) "
            "VALUES (1, 2, '2026-10-01', 850000, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        count = connection.execute("SELECT COUNT(*) FROM rate_history").fetchone()[0]
        assert count == 2

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO rate_history "
                "(employee_id, assignment_id, effective_from, unit_price, "
                "created_at, updated_at) "
                "VALUES (1, 1, '2026-10-01', 900000, ?, ?)",
                (_TIMESTAMP, _TIMESTAMP),
            )


def test_work_period_referenced_work_status_cannot_be_deleted(db_path: Path) -> None:
    """他のテーブル（work_period）から参照されている稼働状態を削除する（TC-DBD-007）。"""
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO work_status "
            "(code, name, is_billable, is_cost_target, sort_order, created_at, updated_at) "
            "VALUES ('TEST_DEV', '開発稼働', 1, 1, 1, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO work_period (employee_id, start_date, work_status_code, "
            "created_at, updated_at) "
            "VALUES (1, '2026-10-01', 'TEST_DEV', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM work_status WHERE code = 'TEST_DEV'")


def test_rate_history_referenced_assignment_cannot_be_deleted(db_path: Path) -> None:
    """他のテーブル（rate_history）から参照されている参画を削除する（TC-DBD-007）。"""
    with open_connection(db_path) as connection:
        _insert_employee(connection, 1)
        connection.execute(
            "INSERT INTO assignment "
            "(employee_id, customer_name, category, start_date, created_at, updated_at) "
            "VALUES (1, '顧客A', 'SES', '2026-10-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            "INSERT INTO rate_history "
            "(employee_id, assignment_id, effective_from, unit_price, created_at, updated_at) "
            "VALUES (1, 1, '2026-10-01', 800000, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM assignment WHERE id = 1")
