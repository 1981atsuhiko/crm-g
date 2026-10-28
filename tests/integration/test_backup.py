"""バックアップ・復元の試験（ARC-021）。"""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

import crm_g.infrastructure.backup as backup_module
from crm_g.infrastructure.backup import (
    BackupIntegrityError,
    BackupWriteError,
    create_backup,
    delete_backup_file,
    list_backup_files,
    restore_backup,
    restore_database,
    verify_backup_integrity,
    write_backup,
)


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


def _read_value(db_path: Path) -> str:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("SELECT value FROM record WHERE id = 1").fetchone()
        return str(row[0])
    finally:
        connection.close()


def _set_value(db_path: Path, value: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE record SET value = ? WHERE id = 1", (value,))
        connection.commit()
    finally:
        connection.close()


def test_write_backup_creates_default_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    backup_dir = tmp_path / "backups"
    fixed_time = datetime(2026, 10, 23, 9, 30, 15, tzinfo=UTC)

    backup_path = write_backup(db_path, backup_dir, create_directory=True, now=fixed_time)

    assert backup_path == backup_dir / "crm-g_20261023_093015.db"
    assert _read_value(backup_path) == "before"


def test_write_backup_does_not_create_specified_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    missing = tmp_path / "missing"

    with pytest.raises(BackupWriteError) as excinfo:
        write_backup(db_path, missing, create_directory=False)

    assert not missing.exists()
    assert str(tmp_path) not in str(excinfo.value)


def test_write_backup_into_existing_specified_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    destination = tmp_path / "usb"
    destination.mkdir()

    backup_path = write_backup(db_path, destination, create_directory=False)

    assert backup_path.parent == destination
    assert _read_value(backup_path) == "before"


def test_write_backup_removes_partial_file_and_hides_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """書き出しに失敗したら、作りかけのファイルを消し、パスを含まないエラーにする。"""
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    destination = tmp_path / "dest"
    destination.mkdir()

    def _fail(source_path: Path, backup_path: Path) -> None:
        backup_path.write_bytes(b"")
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(backup_module, "_copy_database", _fail)

    with pytest.raises(BackupWriteError) as excinfo:
        write_backup(db_path, destination, create_directory=False)

    assert list(destination.iterdir()) == []
    assert str(tmp_path) not in str(excinfo.value)


def test_write_backup_wraps_os_error_before_file_is_decided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)

    def _fail_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "mkdir", _fail_mkdir)

    with pytest.raises(BackupWriteError):
        write_backup(db_path, tmp_path / "backups", create_directory=True)


def test_write_backup_keeps_error_when_partial_file_cannot_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    destination = tmp_path / "dest"
    destination.mkdir()

    def _fail(source_path: Path, backup_path: Path) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    def _fail_remove(path: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(backup_module, "_copy_database", _fail)
    monkeypatch.setattr(backup_module, "delete_backup_file", _fail_remove)

    with pytest.raises(BackupWriteError):
        write_backup(db_path, destination, create_directory=False)


def test_write_backup_rejects_naive_now(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="タイムゾーン"):
        write_backup(
            tmp_path / "crm-g.db",
            tmp_path,
            create_directory=True,
            now=datetime(2026, 10, 23, 9, 30, 15),
        )


def test_delete_backup_file_ignores_missing_file(tmp_path: Path) -> None:
    target = tmp_path / "crm-g_20261023_093015.db"
    target.write_bytes(b"x")

    delete_backup_file(target)
    delete_backup_file(target)

    assert not target.exists()


def test_list_backup_files_returns_backups_newest_first(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for name in (
        "crm-g_20261023_093015.db",
        "crm-g_20261023_093015_1.db",
        "crm-g_20261024_000000.db",
        "crm-g_20261022_235959.db",
    ):
        (backup_dir / name).write_bytes(b"abc")
    # 対象外：形式の違う名前・存在しない日時・フォルダ。
    (backup_dir / "crm-g.db").write_bytes(b"abc")
    (backup_dir / "memo.txt").write_bytes(b"abc")
    (backup_dir / "crm-g_20261399_000000.db").write_bytes(b"abc")
    (backup_dir / "crm-g_20261025_000000.db").mkdir()

    files = list_backup_files(backup_dir)

    assert [file.name for file in files] == [
        "crm-g_20261024_000000.db",
        "crm-g_20261023_093015_1.db",
        "crm-g_20261023_093015.db",
        "crm-g_20261022_235959.db",
    ]
    assert files[0].created_at == datetime(2026, 10, 24, 0, 0, 0, tzinfo=UTC)
    assert files[0].size_bytes == 3
    assert files[0].path == backup_dir / "crm-g_20261024_000000.db"


def test_list_backup_files_returns_empty_when_directory_missing(tmp_path: Path) -> None:
    assert list_backup_files(tmp_path / "missing") == ()


def test_verify_backup_integrity_accepts_valid_file(tmp_path: Path) -> None:
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    before = db_path.read_bytes()

    verify_backup_integrity(db_path)

    assert db_path.read_bytes() == before


def test_verify_backup_integrity_rejects_non_sqlite_file(tmp_path: Path) -> None:
    path = tmp_path / "crm-g_20261023_093015.db"
    path.write_bytes(b"this is not a sqlite database file" * 100)

    with pytest.raises(BackupIntegrityError):
        verify_backup_integrity(path)


def test_verify_backup_integrity_rejects_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.db"

    with pytest.raises(BackupIntegrityError):
        verify_backup_integrity(path)
    assert not path.exists()


def test_verify_backup_integrity_rejects_inconsistent_file(tmp_path: Path) -> None:
    """`PRAGMA integrity_check` が ok 以外を返すファイルを拒否する。"""
    path = tmp_path / "broken.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("CREATE INDEX item_value_idx ON item (value)")
        connection.executemany(
            "INSERT INTO item (value) VALUES (?)", [(f"value-{index}",) for index in range(500)]
        )
        connection.commit()
        # 索引の定義だけを消し、どこからも参照されないページを残す。
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute("DELETE FROM sqlite_master WHERE type = 'index'")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(BackupIntegrityError, match="整合性"):
        verify_backup_integrity(path)


def test_restore_database_replaces_content_and_keeps_backup(tmp_path: Path) -> None:
    """`Connection.backup` で置き換える。バックアップのファイルは書き換えない。"""
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    backup_path = create_backup(db_path, tmp_path / "backups")
    backup_bytes = backup_path.read_bytes()
    _set_value(db_path, "after")

    restore_database(backup_path, db_path)

    assert _read_value(db_path) == "before"
    assert backup_path.read_bytes() == backup_bytes
    assert not (tmp_path / "crm-g.db-journal").exists()


def test_restore_database_while_other_connection_is_open(tmp_path: Path) -> None:
    """他のタブの接続が開いたままでも置き換えられ、その接続から新しい内容が見える。"""
    db_path = tmp_path / "crm-g.db"
    _create_db_with_one_row(db_path)
    backup_path = create_backup(db_path, tmp_path / "backups")
    _set_value(db_path, "after")

    other = sqlite3.connect(db_path)
    try:
        assert other.execute("SELECT value FROM record").fetchone()[0] == "after"
        restore_database(backup_path, db_path)
        assert other.execute("SELECT value FROM record").fetchone()[0] == "before"
    finally:
        other.close()
