"""名寄せの辞書を DB から作る試験（移行設計書 `MIG-031`）。値はすべて架空である。"""

from pathlib import Path

import pytest

from crm_g.application.import_matching import load_employee_directory, match_employee
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """マイグレーションを適用した一時 DB を作る。"""
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def test_directory_includes_all_employees_including_retired(db_path: Path) -> None:
    """名寄せの対象は、退職者を含む DB の全社員。"""
    with open_connection(db_path) as connection:
        connection.executemany(
            "INSERT INTO employee (id, email, name, enrollment_status, join_date, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "taro@example.com", "山田 太郎", "在籍", "2020-04-01", _TIMESTAMP, _TIMESTAMP),
                (2, "jiro@example.com", "鈴木 次郎", "退職", "2019-04-01", _TIMESTAMP, _TIMESTAMP),
            ],
        )
        connection.commit()

        directory = load_employee_directory(connection)

    assert match_employee(directory, 2, "山田\u3000太郎", None).employee_id == 1
    assert match_employee(directory, 3, "鈴木次郎", None).employee_id == 2
    assert match_employee(directory, 4, None, "JIRO@example.com").employee_id == 2
