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
    DatabaseNotFoundError,
    DuplicateMigrationVersionError,
    InvalidDatabaseFileError,
    InvalidMigrationFilenameError,
    MigrationFailedError,
    PendingMigrationError,
    UnknownMigrationError,
    apply_migrations,
    verify_database_file,
    verify_schema_is_current,
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
        rows = connection.execute("SELECT name FROM schema_migration ORDER BY version").fetchall()
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
        names = [row[0] for row in restored.execute("SELECT name FROM schema_migration").fetchall()]
        assert names == ["001_create_a"]
    finally:
        restored.close()


def test_apply_migrations_rejects_filename_without_numeric_prefix(tmp_path: Path) -> None:
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    connection = sqlite3.connect(tmp_path / "test.db")

    try:
        with pytest.raises(InvalidMigrationFilenameError, match="create_a"):
            apply_migrations(connection, ddl_dir, tmp_path / "test.db", tmp_path / "backups")
    finally:
        connection.close()


def test_apply_migrations_rejects_duplicate_version(tmp_path: Path) -> None:
    """ゼロ埋めの桁数が異なるファイルが、同じ版番号を指す場合を拒否する。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write_sql(ddl_dir, "1_create_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    connection = sqlite3.connect(tmp_path / "test.db")

    try:
        with pytest.raises(DuplicateMigrationVersionError, match="1"):
            apply_migrations(connection, ddl_dir, tmp_path / "test.db", tmp_path / "backups")
    finally:
        connection.close()


def test_apply_migrations_orders_by_integer_version_not_filename_text(tmp_path: Path) -> None:
    """ゼロ埋めの桁数が揃わないファイルでも、整数の版番号の順に適用する。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "2_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write_sql(ddl_dir, "10_create_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    connection = sqlite3.connect(tmp_path / "test.db")

    try:
        applied = apply_migrations(connection, ddl_dir, tmp_path / "test.db", tmp_path / "backups")

        assert applied == ("2_create_a", "10_create_b")
    finally:
        connection.close()


def _migrated_db(tmp_path: Path, ddl_dir: Path) -> Path:
    """`ddl_dir` の連番SQLをすべて適用した DB を作る。"""
    db_path = tmp_path / "verify.db"
    connection = sqlite3.connect(db_path)
    try:
        apply_migrations(connection, ddl_dir, db_path, tmp_path / "backups")
    finally:
        connection.close()
    return db_path


def _snapshot(db_path: Path) -> bytes:
    return db_path.read_bytes()


def test_verify_database_file_accepts_current_and_older_versions(tmp_path: Path) -> None:
    """同梱のDDLと同じ版・古い版は通る。検査でファイルは書き換わらない。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = _migrated_db(tmp_path, ddl_dir)
    before = _snapshot(db_path)

    verify_database_file(db_path, ddl_dir)
    _write_sql(ddl_dir, "002_create_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    verify_database_file(db_path, ddl_dir)

    assert _snapshot(db_path) == before


def test_verify_database_file_rejects_newer_version(tmp_path: Path) -> None:
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write_sql(ddl_dir, "002_create_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    db_path = _migrated_db(tmp_path, ddl_dir)
    (ddl_dir / "002_create_b.sql").unlink()

    with pytest.raises(UnknownMigrationError, match="002_create_b"):
        verify_database_file(db_path, ddl_dir)


def test_verify_database_file_rejects_checksum_mismatch(tmp_path: Path) -> None:
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = _migrated_db(tmp_path, ddl_dir)
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER);")

    with pytest.raises(ChecksumMismatchError, match="001_create_a"):
        verify_database_file(db_path, ddl_dir)


def test_verify_database_file_rejects_db_without_schema_migration(tmp_path: Path) -> None:
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "other.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(InvalidDatabaseFileError):
        verify_database_file(db_path, ddl_dir)


def test_verify_database_file_rejects_empty_schema_migration(tmp_path: Path) -> None:
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "empty.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            "checksum TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(InvalidDatabaseFileError):
        verify_database_file(db_path, ddl_dir)


def test_verify_database_file_rejects_non_sqlite_file(tmp_path: Path) -> None:
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "text.db"
    db_path.write_bytes(b"this is not a sqlite database file" * 100)

    with pytest.raises(InvalidDatabaseFileError):
        verify_database_file(db_path, ddl_dir)


def test_verify_database_file_rejects_missing_file_without_creating_it(tmp_path: Path) -> None:
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "missing.db"

    with pytest.raises(InvalidDatabaseFileError):
        verify_database_file(db_path, ddl_dir)
    assert not db_path.exists()


def test_verify_schema_is_current_accepts_latest_without_changing_db(tmp_path: Path) -> None:
    """同梱のDDLと同じ版は通る。検査で DB は書き換わらない（移行コマンドの前の検査）。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = _migrated_db(tmp_path, ddl_dir)
    before = _snapshot(db_path)

    verify_schema_is_current(db_path, ddl_dir)

    assert _snapshot(db_path) == before


def test_verify_schema_is_current_rejects_pending_migration(tmp_path: Path) -> None:
    """未適用の連番SQLがあるときはエラーにし、適用しない（DB を変えない）。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = _migrated_db(tmp_path, ddl_dir)
    _write_sql(ddl_dir, "002_create_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    before = _snapshot(db_path)

    with pytest.raises(PendingMigrationError, match="002_create_b"):
        verify_schema_is_current(db_path, ddl_dir)
    assert _snapshot(db_path) == before


def test_verify_schema_is_current_rejects_missing_file_without_creating_it(
    tmp_path: Path,
) -> None:
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = tmp_path / "missing.db"

    with pytest.raises(DatabaseNotFoundError):
        verify_schema_is_current(db_path, ddl_dir)
    assert not db_path.exists()


def test_verify_schema_is_current_rejects_checksum_mismatch(tmp_path: Path) -> None:
    """適用済みの連番SQLの中身が変わっていたら、エラーにする（DB を変えない）。"""
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db_path = _migrated_db(tmp_path, ddl_dir)
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY, x TEXT);")
    before = _snapshot(db_path)

    with pytest.raises(ChecksumMismatchError, match="001_create_a"):
        verify_schema_is_current(db_path, ddl_dir)
    assert _snapshot(db_path) == before


def test_verify_schema_is_current_rejects_newer_version_and_invalid_file(tmp_path: Path) -> None:
    ddl_dir = tmp_path / "ddl"
    _write_sql(ddl_dir, "001_create_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write_sql(ddl_dir, "002_create_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    db_path = _migrated_db(tmp_path, ddl_dir)
    (ddl_dir / "002_create_b.sql").unlink()
    text_path = tmp_path / "text.db"
    text_path.write_bytes(b"this is not a sqlite database file" * 100)

    with pytest.raises(UnknownMigrationError):
        verify_schema_is_current(db_path, ddl_dir)
    with pytest.raises(InvalidDatabaseFileError):
        verify_schema_is_current(text_path, ddl_dir)
