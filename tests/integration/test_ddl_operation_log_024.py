"""DDL/024（操作ログの operation に「バックアップ作成」「復元」を加える）の試験。

実際の DDL/ 配下の連番SQLに対して実施する（実装計画書5.3節）。表の作り直しで、
行・id・AUTOINCREMENT の採番（sqlite_sequence）・追記専用のトリガー・索引が
保たれることを確かめる（画面設計書 `SCN-065`、14章-11。ユーザー承認 2026-10-27）。
"""

import sqlite3
from pathlib import Path

import pytest

from crm_g.application.operation_log import Operation
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_MIGRATION_024 = "024_add_backup_restore_operations.sql"
_TIMESTAMP = "2026-10-21T00:00:00.000000Z"


def _copy_ddl_until(target_dir: Path, last_version: int) -> None:
    """同梱のDDLのうち、指定した版までを複写する。"""
    target_dir.mkdir()
    for source in sorted(_DDL_DIR.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= last_version:
            (target_dir / source.name).write_bytes(source.read_bytes())


def _insert_log(connection: sqlite3.Connection, log_id: int | None, operation: str) -> None:
    """操作ログに1行を入れる。`log_id` が `None` のときは、採番に任せる。"""
    connection.execute(
        "INSERT INTO operation_log "
        "(id, occurred_at, operation, target_table, target_key, detail, before_value, after_value) "
        "VALUES (?, ?, ?, 'salary_history', '7', '要約', '{\"a\": 1}', '{\"a\": 2}')",
        (log_id, _TIMESTAMP, operation),
    )


def _schema_objects(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    """operation_log に属する、トリガーと索引の (種類, 名前) の組。"""
    rows = connection.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE tbl_name = 'operation_log' AND type IN ('trigger', 'index') "
        "AND name NOT LIKE 'sqlite_autoindex%'"
    ).fetchall()
    return {(row[0], row[1]) for row in rows}


def _sequence(connection: sqlite3.Connection) -> list[tuple[str, int]]:
    """sqlite_sequence の operation_log 関係の行。"""
    rows = connection.execute(
        "SELECT name, seq FROM sqlite_sequence WHERE name LIKE 'operation_log%' ORDER BY name"
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


@pytest.fixture
def old_db(tmp_path: Path) -> tuple[Path, Path]:
    """023 までを適用した DB と、その DDL のフォルダ。"""
    ddl_dir = tmp_path / "ddl"
    _copy_ddl_until(ddl_dir, 23)
    path = tmp_path / "old.db"
    with open_connection(path) as connection:
        apply_migrations(connection, ddl_dir, path, tmp_path / "backups")
    return path, ddl_dir


def _apply_024(path: Path, ddl_dir: Path, backup_dir: Path) -> tuple[str, ...]:
    """024 を DDL のフォルダに加えて適用する。"""
    (ddl_dir / _MIGRATION_024).write_bytes((_DDL_DIR / _MIGRATION_024).read_bytes())
    with open_connection(path) as connection:
        return apply_migrations(connection, ddl_dir, path, backup_dir)


def test_migration_024_keeps_rows_ids_sequence_triggers_and_indexes(
    old_db: tuple[Path, Path], tmp_path: Path
) -> None:
    path, ddl_dir = old_db
    with open_connection(path) as connection:
        _insert_log(connection, 1, "閲覧")
        _insert_log(connection, 2, "更新")
        _insert_log(connection, 10, "パスコード設定")
        # 取り消された追加などで、採番が行の最大の id より先に進んでいる状態にする。
        connection.execute("UPDATE sqlite_sequence SET seq = 25 WHERE name = 'operation_log'")
        before_rows = connection.execute("SELECT * FROM operation_log ORDER BY id").fetchall()
        before_objects = _schema_objects(connection)

    applied = _apply_024(path, ddl_dir, tmp_path / "backups")

    assert applied == ("024_add_backup_restore_operations",)
    with open_connection(path) as connection:
        assert connection.execute("SELECT * FROM operation_log ORDER BY id").fetchall() == (
            before_rows
        )
        assert _schema_objects(connection) == before_objects
        assert _sequence(connection) == [("operation_log", 25)]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "operation_log_new" not in tables

        # 採番は、引き継いだ値の続きから行う。
        _insert_log(connection, None, "復元")
        new_id = connection.execute("SELECT MAX(id) FROM operation_log").fetchone()[0]
        assert new_id == 26

        # 追記専用のトリガーが働く（DBD-019）。
        with pytest.raises(sqlite3.IntegrityError, match="追記専用"):
            connection.execute("UPDATE operation_log SET detail = 'x' WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError, match="追記専用"):
            connection.execute("DELETE FROM operation_log WHERE id = 1")


def test_migration_024_on_empty_operation_log(old_db: tuple[Path, Path], tmp_path: Path) -> None:
    """操作ログが空のとき（採番の行がないとき）も適用でき、id は 1 から振られる。"""
    path, ddl_dir = old_db

    _apply_024(path, ddl_dir, tmp_path / "backups")

    with open_connection(path) as connection:
        assert _sequence(connection) == []
        _insert_log(connection, None, "バックアップ作成")
        assert connection.execute("SELECT id FROM operation_log").fetchall() == [(1,)]


def test_old_schema_rejects_new_operations(old_db: tuple[Path, Path]) -> None:
    """023 までの CHECK 制約は、新しい2つの値を受け付けない（024 が必要な理由）。"""
    path, _ = old_db
    with open_connection(path) as connection, pytest.raises(sqlite3.IntegrityError):
        _insert_log(connection, None, "復元")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def test_check_constraint_accepts_every_operation_value(db_path: Path) -> None:
    """`Operation` のすべての値を、DB の CHECK 制約が受け付ける（DBD-020）。"""
    with open_connection(db_path) as connection:
        for operation in Operation:
            _insert_log(connection, None, operation.value)
        count = connection.execute("SELECT COUNT(*) FROM operation_log").fetchone()[0]
    assert count == len(Operation)


def test_check_constraint_rejects_undefined_operation(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        _insert_log(connection, None, "未定義の操作")


def test_rebuilt_table_still_rejects_invalid_json_and_timestamp(db_path: Path) -> None:
    """作り直した表でも、JSON と日時の形式の検査が働く。"""
    with open_connection(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO operation_log (occurred_at, operation, target_table, before_value) "
                "VALUES (?, '復元', 'database', 'not json')",
                (_TIMESTAMP,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO operation_log (occurred_at, operation, target_table) "
                "VALUES ('2026-10-21 00:00:00', '復元', 'database')"
            )
