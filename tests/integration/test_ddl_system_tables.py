"""DDL（システムテーブル・初期データ）の試験（TC-DBD-017）。

実際の DDL/ 配下の連番SQLに対して実施する（実装計画書5.3節）。
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


def _count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
    return int(row[0])


def test_tc_dbd_017_initial_data_is_inserted(db_path: Path) -> None:
    """空のDBにマイグレーションを適用する（TC-DBD-017）。"""
    with open_connection(db_path) as connection:
        assert _count(connection, "work_status") == 9
        assert _count(connection, "pay_item") == 9
        assert _count(connection, "system_setting") == 2
        assert _count(connection, "bonus_rule") == 2
        assert _count(connection, "business_calendar") == 0

        bonus_rule_base_types = {
            row[0] for row in connection.execute("SELECT base_type FROM bonus_rule").fetchall()
        }
        assert bonus_rule_base_types == {"基本給", "月給合計"}

        cost_coefficient = connection.execute(
            "SELECT value_integer FROM system_setting WHERE setting_key = '原価係数'"
        ).fetchone()
        assert cost_coefficient[0] == 12000

        rounding = connection.execute(
            "SELECT value_text FROM system_setting WHERE setting_key = '端数処理'"
        ).fetchone()
        assert rounding[0] == "切り上げ"


def test_operation_log_rejects_update(db_path: Path) -> None:
    """operation_log は追記専用のため、UPDATEが拒否される（DBD-019）。"""
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO operation_log (occurred_at, operation, target_table) "
            "VALUES (?, '閲覧', 'employee')",
            (_TIMESTAMP,),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE operation_log SET detail = 'x' WHERE id = 1")


def test_operation_log_rejects_delete(db_path: Path) -> None:
    """operation_log は追記専用のため、DELETEが拒否される（DBD-019）。"""
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO operation_log (occurred_at, operation, target_table) "
            "VALUES (?, '閲覧', 'employee')",
            (_TIMESTAMP,),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM operation_log WHERE id = 1")


def test_operation_log_rejects_invalid_json_in_before_value(db_path: Path) -> None:
    """before_value は、JSONとして妥当な文字列だけを許す（json_valid）。"""
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO operation_log "
            "(occurred_at, operation, target_table, before_value) "
            "VALUES (?, '更新', 'employee', 'not json')",
            (_TIMESTAMP,),
        )


def test_operation_log_accepts_valid_json_in_before_value(db_path: Path) -> None:
    """before_value に妥当なJSON文字列を保存できる。"""
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO operation_log "
            "(occurred_at, operation, target_table, before_value) "
            "VALUES (?, '更新', 'employee', '{\"name\": \"山田太郎\"}')",
            (_TIMESTAMP,),
        )
        connection.commit()

        row = connection.execute("SELECT before_value FROM operation_log").fetchone()
        assert row is not None


def test_app_credential_rejects_id_other_than_one(db_path: Path) -> None:
    """app_credential は、id=1の行だけを持つ（1行だけのテーブル）。"""
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO app_credential "
            "(id, algorithm, scrypt_n, scrypt_r, scrypt_p, salt, hash, "
            "created_at, updated_at) "
            "VALUES (2, 'scrypt', 131072, 8, 1, ?, ?, ?, ?)",
            ("a" * 32, "b" * 64, _TIMESTAMP, _TIMESTAMP),
        )


@pytest.mark.parametrize(
    ("salt", "hash_value"),
    [
        ("a" * 31, "b" * 64),  # saltが1文字短い
        ("a" * 33, "b" * 64),  # saltが1文字長い
        ("g" * 32, "b" * 64),  # saltに16進数以外の文字（g）
        ("A" * 32, "b" * 64),  # saltが大文字の16進数
        ("a" * 32, "b" * 63),  # hashが1文字短い
        ("a" * 32, "b" * 65),  # hashが1文字長い
    ],
)
def test_app_credential_rejects_invalid_salt_or_hash(
    db_path: Path, salt: str, hash_value: str
) -> None:
    """saltは32文字、hashは64文字の、小文字の16進数だけを許す

    （データ設計書8.3節。hashlibの.hexdigest()の出力形式と一致させる）。
    """
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO app_credential "
            "(id, algorithm, scrypt_n, scrypt_r, scrypt_p, salt, hash, "
            "created_at, updated_at) "
            "VALUES (1, 'scrypt', 131072, 8, 1, ?, ?, ?, ?)",
            (salt, hash_value, _TIMESTAMP, _TIMESTAMP),
        )


def test_app_credential_accepts_valid_row(db_path: Path) -> None:
    """32文字・64文字の小文字16進数のsalt・hashを受理する。"""
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO app_credential "
            "(id, algorithm, scrypt_n, scrypt_r, scrypt_p, salt, hash, "
            "created_at, updated_at) "
            "VALUES (1, 'scrypt', 131072, 8, 1, ?, ?, ?, ?)",
            ("a" * 32, "b" * 64, _TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        row = connection.execute("SELECT failed_count FROM app_credential").fetchone()
        assert row[0] == 0
