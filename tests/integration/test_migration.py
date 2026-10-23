"""マイグレーションの試験（TC-ARC-009〜014）。

実装計画書5.3節のとおり、PH-03では試験用の連番SQLで実施する。実際のDDL/で
通ることの確認は、PH-04以降で行う。
"""

import sqlite3
from pathlib import Path

import pytest

from crm_g.infrastructure.backup import restore_backup
from crm_g.infrastructure.migration import (
    ChecksumMismatchError,
    MigrationFailedError,
    UnknownMigrationError,
    apply_migrations,
)


def _write_sql(ddl_dir: Path, name: str, content: str) -> None:
    ddl_dir.mkdir(parents=True, exist_ok=True)
    (ddl_dir / name).write_text(content, encoding="utf-8")


def test_tc_arc_009_initial_migration_is_applied_in_order(tmp_path: Path) -> None:
    """空のDBで、連番SQLを適用する（TC-ARC-009）。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write_sql(ddl_dir, "002_create_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "test.db"
    connection = sqlite3.connect(db_path)

    try:
        applied = apply_migrations(connection, ddl_dir, db_path, tmp_path / "backups")

        assert applied == ("001_create_a", "002_create_b")
        rows = connection.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        ).fetchall()
        assert [row[0] for row in rows] == ["001_create_a", "002_create_b"]
        connection.execute("SELECT * FROM a")
        connection.execute("SELECT * FROM b")
    finally:
        connection.close()


def test_tc_arc_010_already_applied_migration_runs_nothing(tmp_path: Path) -> None:
    """適用済みのDBで、もう一度起動する（TC-ARC-010）。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "test.db"
    connection = sqlite3.connect(db_path)

    try:
        apply_migrations(connection, ddl_dir, db_path, tmp_path / "backups")
        before = connection.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0]

        applied = apply_migrations(connection, ddl_dir, db_path, tmp_path / "backups")

        after = connection.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0]
        assert applied == ()
        assert before == after == 1
    finally:
        connection.close()


def test_tc_arc_011_checksum_mismatch_aborts_startup(tmp_path: Path) -> None:
    """適用済みの連番SQLの内容を書き換えて起動する（TC-ARC-011）。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "test.db"
    connection = sqlite3.connect(db_path)

    try:
        apply_migrations(connection, ddl_dir, db_path, tmp_path / "backups")

        _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY, x TEXT);")

        with pytest.raises(ChecksumMismatchError, match="001_create_a"):
            apply_migrations(connection, ddl_dir, db_path, tmp_path / "backups")
    finally:
        connection.close()


def test_tc_arc_012_db_newer_than_app_aborts_startup(tmp_path: Path) -> None:
    """アプリの対応版より新しい版のDBで起動する（TC-ARC-012）。DBは変更されない。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write_sql(ddl_dir, "002_create_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "test.db"
    connection = sqlite3.connect(db_path)

    try:
        apply_migrations(connection, ddl_dir, db_path, tmp_path / "backups")
        before = connection.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0]

        (ddl_dir / "002_create_b.sql").unlink()

        with pytest.raises(UnknownMigrationError, match="002_create_b"):
            apply_migrations(connection, ddl_dir, db_path, tmp_path / "backups")

        after = connection.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0]
        assert before == after
    finally:
        connection.close()


def test_tc_arc_013_backup_is_created_before_applying(tmp_path: Path) -> None:
    """未適用の連番SQLがあるDBで起動する（TC-ARC-013）。適用の前にバックアップが作られる。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "test.db"
    backup_dir = tmp_path / "backups"
    connection = sqlite3.connect(db_path)

    try:
        apply_migrations(connection, ddl_dir, db_path, backup_dir)

        backups = list(backup_dir.glob("crm-g_*.db"))
        assert len(backups) == 1
    finally:
        connection.close()


def test_tc_arc_014_failed_migration_can_be_restored_from_backup(tmp_path: Path) -> None:
    """連番SQLの1つを、失敗する内容にして起動する（TC-ARC-014）。

    起動が中止され、バックアップから復元すると、適用前の状態に戻る。
    """
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "test.db"
    backup_dir = tmp_path / "backups"
    connection = sqlite3.connect(db_path)
    apply_migrations(connection, ddl_dir, db_path, backup_dir)

    _write_sql(ddl_dir, "002_broken.sql", "THIS IS NOT VALID SQL;")

    with pytest.raises(MigrationFailedError, match="002_broken") as excinfo:
        apply_migrations(connection, ddl_dir, db_path, backup_dir)
    connection.close()

    restore_backup(excinfo.value.backup_path, db_path)

    restored = sqlite3.connect(db_path)
    try:
        versions = [
            row[0] for row in restored.execute("SELECT version FROM schema_migration").fetchall()
        ]
        assert versions == ["001_create_a"]
    finally:
        restored.close()
