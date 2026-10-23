"""バックアップ・復元の試験（ARC-021）。"""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from crm_g.infrastructure.backup import create_backup, restore_backup


def _create_db_with_one_row(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE record (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO record (id, value) VALUES (1, 'before')")
        connection.commit()
    finally:
        connection.close()


def test_create_backup_uses_expected_filename_pattern(tmp_path: Path) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    fixed_time = datetime(2026, 10, 23, 9, 30, 15, tzinfo=UTC)

    backup_path = create_backup(db_path, tmp_path / "backups", now=fixed_time)

    assert backup_path.name == "crm-g_20261023_093015.db"
    assert backup_path.exists()


def test_create_backup_copies_current_data(tmp_path: Path) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)

    backup_path = create_backup(db_path, tmp_path / "backups")

    connection = sqlite3.connect(backup_path)
    try:
        row = connection.execute("SELECT value FROM record WHERE id = 1").fetchone()
        assert row[0] == "before"
    finally:
        connection.close()


def test_restore_backup_reverts_db_to_backed_up_state(tmp_path: Path) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    backup_path = create_backup(db_path, tmp_path / "backups")

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE record SET value = 'after' WHERE id = 1")
        connection.commit()
    finally:
        connection.close()

    restore_backup(backup_path, db_path)

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("SELECT value FROM record WHERE id = 1").fetchone()
        assert row[0] == "before"
    finally:
        connection.close()


def test_create_backup_does_not_overwrite_existing_backup_in_same_second(
    tmp_path: Path,
) -> None:
    """同じ秒に複数回バックアップしても、既存のバックアップを上書きしない。"""
    db_path = tmp_path / "crm-g.db"
    backup_dir = tmp_path / "backups"
    fixed_time = datetime(2026, 10, 23, 9, 30, 15, tzinfo=UTC)

    _create_db_with_one_row(db_path)
    first_backup = create_backup(db_path, backup_dir, now=fixed_time)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE record SET value = 'second' WHERE id = 1")
        connection.commit()
    finally:
        connection.close()
    second_backup = create_backup(db_path, backup_dir, now=fixed_time)

    assert first_backup != second_backup

    connection = sqlite3.connect(first_backup)
    try:
        row = connection.execute("SELECT value FROM record WHERE id = 1").fetchone()
        assert row[0] == "before"
    finally:
        connection.close()


def test_create_backup_converts_non_utc_timezone_to_utc(tmp_path: Path) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    jst = timezone(timedelta(hours=9))
    jst_time = datetime(2026, 10, 23, 18, 30, 15, tzinfo=jst)

    backup_path = create_backup(db_path, tmp_path / "backups", now=jst_time)

    assert backup_path.name == "crm-g_20261023_093015.db"


def test_create_backup_rejects_naive_now(tmp_path: Path) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)

    with pytest.raises(ValueError, match="タイムゾーン"):
        create_backup(db_path, tmp_path / "backups", now=datetime(2026, 10, 23, 9, 30, 15))
