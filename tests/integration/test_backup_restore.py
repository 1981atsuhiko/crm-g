"""バックアップ・復元のユースケースの試験（`SCN-065`、`TC-SCN-021`、`TC-E2E-005` の前提）。

復元は、実際の SQLite のファイルで行う（テスト計画書 `TC-SCN-021`）。
"""

import sqlite3
from pathlib import Path

import pytest

import crm_g.application.backup as backup_app
import crm_g.infrastructure.backup as infra_backup
from crm_g.application.auth import setup_passcode
from crm_g.application.backup import (
    BACKUP_TARGET_TABLE,
    BackupCreationError,
    BackupDestinationError,
    BackupNotFoundError,
    InvalidBackupError,
    RestoreFailedError,
    create_manual_backup,
    list_backups,
    parse_backup_destination,
    restore_from_backup,
)
from crm_g.infrastructure.backup import BackupWriteError, create_backup
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_PASSCODE = "taro@3sss.co.jp"
_COST_KEY = "原価係数"


class _Paths:
    """試験用のデータ領域（DB とバックアップのフォルダ）。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.db_path = root / "crm-g.db"
        self.backup_dir = root / "backups"


@pytest.fixture
def paths(tmp_path: Path) -> _Paths:
    """マイグレーションを適用し、パスコードを設定した DB を作る。"""
    data = _Paths(tmp_path / "crm-g")
    data.root.mkdir()
    with open_connection(data.db_path) as connection:
        apply_migrations(connection, _DDL_DIR, data.db_path, data.backup_dir)
        setup_passcode(connection, _PASSCODE, _PASSCODE)
    # マイグレーションの前の自動のバックアップは、試験の対象外のため消す。
    for file in data.backup_dir.glob("*.db"):
        file.unlink()
    return data


def _cost_coefficient(db_path: Path) -> int:
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT value_integer FROM system_setting WHERE setting_key = ?", (_COST_KEY,)
        ).fetchone()
    return int(row[0])


def _set_cost_coefficient(db_path: Path, value: int) -> None:
    with open_connection(db_path) as connection:
        connection.execute(
            "UPDATE system_setting SET value_integer = ? WHERE setting_key = ?",
            (value, _COST_KEY),
        )


def _logs(db_path: Path, operation: str) -> list[tuple[str, str | None]]:
    """指定した操作の (target_table, detail) を、記録の順に返す。"""
    with open_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT target_table, detail FROM operation_log WHERE operation = ? ORDER BY id",
            (operation,),
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _backup_names(backup_dir: Path) -> set[str]:
    return {path.name for path in backup_dir.glob("*.db")}


def _write_raw(path: Path, statements: list[str]) -> None:
    """生の sqlite3 で、ファイルに SQL を実行する（不正なバックアップを作るため）。"""
    connection = sqlite3.connect(path)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


# --- 一覧・作成 ---------------------------------------------------------------


def test_list_backups_is_empty_without_backups(paths: _Paths) -> None:
    assert list_backups(paths.backup_dir) == ()


def test_create_manual_backup_in_default_directory_and_records_log(paths: _Paths) -> None:
    name = create_manual_backup(paths.db_path, paths.backup_dir, None)

    entries = list_backups(paths.backup_dir)
    assert [entry.name for entry in entries] == [name]
    assert entries[0].size_bytes > 0
    assert entries[0].created_at.tzinfo is not None
    # detail はファイル名だけ（パスを入れない）。
    assert _logs(paths.db_path, "バックアップ作成") == [(BACKUP_TARGET_TABLE, name)]


def test_create_manual_backup_into_specified_directory(paths: _Paths, tmp_path: Path) -> None:
    destination = tmp_path / "usb"
    destination.mkdir()

    name = create_manual_backup(paths.db_path, paths.backup_dir, destination)

    assert (destination / name).is_file()
    # 一覧・復元の対象は、既定の保存場所だけ。
    assert list_backups(paths.backup_dir) == ()
    logs = _logs(paths.db_path, "バックアップ作成")
    assert logs == [(BACKUP_TARGET_TABLE, name)]
    assert str(destination) not in str(logs)


def test_create_manual_backup_reports_unwritable_destination(
    paths: _Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "readonly"
    destination.mkdir()

    def _fail(*args: object, **kwargs: object) -> Path:
        raise BackupWriteError("バックアップを作成できませんでした")

    monkeypatch.setattr(backup_app, "write_backup", _fail)

    with pytest.raises(BackupDestinationError, match="書き込めません") as excinfo:
        create_manual_backup(paths.db_path, paths.backup_dir, destination)
    assert str(tmp_path) not in str(excinfo.value)
    assert _logs(paths.db_path, "バックアップ作成") == []


def test_create_manual_backup_reports_failure_in_default_directory(
    paths: _Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*args: object, **kwargs: object) -> Path:
        raise BackupWriteError("バックアップを作成できませんでした")

    monkeypatch.setattr(backup_app, "write_backup", _fail)

    with pytest.raises(BackupCreationError):
        create_manual_backup(paths.db_path, paths.backup_dir, None)


def test_create_manual_backup_removes_file_when_log_fails(
    paths: _Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """記録に失敗したら、作ったファイルを消してエラーにする（`SCD-021`）。"""

    def _fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("記録の失敗")

    monkeypatch.setattr(backup_app, "record_operation_log", _fail)

    with pytest.raises(RuntimeError, match="記録の失敗"):
        create_manual_backup(paths.db_path, paths.backup_dir, None)
    assert _backup_names(paths.backup_dir) == set()


# --- 作成先の検証 -------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", '""', '  " "  '])
def test_parse_backup_destination_blank_means_default(text: str) -> None:
    assert parse_backup_destination(text) is None


def test_parse_backup_destination_accepts_existing_absolute_directory(tmp_path: Path) -> None:
    assert parse_backup_destination(f"  {tmp_path}  ") == tmp_path
    # エクスプローラーの「パスのコピー」で付く引用符を除く。
    assert parse_backup_destination(f'"{tmp_path}"') == tmp_path


@pytest.mark.parametrize("text", ["backups", r"..\backups", "C:backups", r"\backups"])
def test_parse_backup_destination_rejects_relative_path(text: str) -> None:
    with pytest.raises(BackupDestinationError, match="ドライブ名"):
        parse_backup_destination(text)


def test_parse_backup_destination_rejects_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(BackupDestinationError, match="見つかりません") as excinfo:
        parse_backup_destination(str(missing))
    assert str(tmp_path) not in str(excinfo.value)
    assert not missing.exists()


def test_parse_backup_destination_rejects_file(tmp_path: Path) -> None:
    file = tmp_path / "file.txt"
    file.write_text("x", encoding="utf-8")

    with pytest.raises(BackupDestinationError, match="見つかりません"):
        parse_backup_destination(str(file))


def test_parse_backup_destination_rejects_null_character(tmp_path: Path) -> None:
    with pytest.raises(BackupDestinationError, match="使えない文字"):
        parse_backup_destination(f"{tmp_path}\x00x")


# --- 復元 ---------------------------------------------------------------------


def test_tc_scn_021_restore_returns_to_backup_and_creates_safety_backup(paths: _Paths) -> None:
    """作成して一覧に出し、データを変えてから復元する（`TC-SCN-021`、`TC-E2E-005`）。"""
    original = _cost_coefficient(paths.db_path)
    name = create_manual_backup(paths.db_path, paths.backup_dir, None)
    _set_cost_coefficient(paths.db_path, original + 500)

    result = restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, name)

    assert result.restored_name == name
    assert result.applied_migrations == ()
    assert _cost_coefficient(paths.db_path) == original
    # 復元の前に、自動のバックアップが作られ、変更後の内容を持つ。
    assert result.safety_backup_name in {entry.name for entry in list_backups(paths.backup_dir)}
    assert _cost_coefficient(paths.backup_dir / result.safety_backup_name) == original + 500
    # 復元の記録は、復元後の DB に追記される。
    assert _logs(paths.db_path, "復元") == [(BACKUP_TARGET_TABLE, name)]
    # 操作ログもバックアップの時点に戻る（バックアップ自身の作成の記録は、作成の後のため含まない）。
    assert _logs(paths.db_path, "バックアップ作成") == []
    assert not (paths.root / "crm-g.db-journal").exists()


def test_restore_keeps_other_connection_usable(paths: _Paths) -> None:
    """他のタブの接続が開いていても復元でき、その接続から復元後の内容が見える。"""
    original = _cost_coefficient(paths.db_path)
    name = create_manual_backup(paths.db_path, paths.backup_dir, None)
    _set_cost_coefficient(paths.db_path, original + 1)

    with open_connection(paths.db_path) as other:
        restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, name)
        row = other.execute(
            "SELECT value_integer FROM system_setting WHERE setting_key = ?", (_COST_KEY,)
        ).fetchone()
    assert row[0] == original


def test_restore_old_version_backup_applies_pending_migrations(
    paths: _Paths, tmp_path: Path
) -> None:
    """古い版（023 まで）のバックアップは、復元の直後に未適用の連番SQLを当てる。"""
    old_ddl = tmp_path / "old_ddl"
    old_ddl.mkdir()
    for source in _DDL_DIR.glob("*.sql"):
        if int(source.name.split("_", 1)[0]) <= 23:
            (old_ddl / source.name).write_bytes(source.read_bytes())
    old_db = tmp_path / "old.db"
    with open_connection(old_db) as connection:
        apply_migrations(connection, old_ddl, old_db, tmp_path / "old_backups")
        setup_passcode(connection, _PASSCODE, _PASSCODE)
    old_backup = create_backup(old_db, paths.backup_dir)

    result = restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, old_backup.name)

    assert result.applied_migrations == ("024_add_backup_restore_operations",)
    assert _logs(paths.db_path, "復元") == [(BACKUP_TARGET_TABLE, old_backup.name)]


@pytest.mark.parametrize("file_name", ["missing.db", "../crm-g.db", "crm-g.db"])
def test_restore_rejects_name_not_in_list(paths: _Paths, file_name: str) -> None:
    create_manual_backup(paths.db_path, paths.backup_dir, None)

    with pytest.raises(BackupNotFoundError):
        restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, file_name)


def _assert_not_restored(paths: _Paths, expected_cost: int, names_before: set[str]) -> None:
    """DB が置き換わっておらず、復元の前のバックアップも作られていないこと。"""
    assert _cost_coefficient(paths.db_path) == expected_cost
    assert _backup_names(paths.backup_dir) == names_before
    assert _logs(paths.db_path, "復元") == []


def test_restore_rejects_non_sqlite_file(paths: _Paths) -> None:
    paths.backup_dir.mkdir(exist_ok=True)
    broken = paths.backup_dir / "crm-g_20261001_000000.db"
    broken.write_bytes(b"this is not a sqlite database file" * 100)
    expected = _cost_coefficient(paths.db_path)
    names = _backup_names(paths.backup_dir)

    with pytest.raises(InvalidBackupError, match="整合性"):
        restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, broken.name)
    _assert_not_restored(paths, expected, names)


def test_restore_rejects_sqlite_file_of_other_application(paths: _Paths) -> None:
    paths.backup_dir.mkdir(exist_ok=True)
    other = paths.backup_dir / "crm-g_20261001_000000.db"
    _write_raw(other, ["CREATE TABLE other (id INTEGER PRIMARY KEY)"])
    expected = _cost_coefficient(paths.db_path)
    names = _backup_names(paths.backup_dir)

    with pytest.raises(InvalidBackupError, match="このアプリのバックアップ"):
        restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, other.name)
    _assert_not_restored(paths, expected, names)


def test_restore_rejects_newer_version(paths: _Paths) -> None:
    name = create_manual_backup(paths.db_path, paths.backup_dir, None)
    _write_raw(
        paths.backup_dir / name,
        [
            "INSERT INTO schema_migration (version, name, checksum, applied_at) "
            "VALUES (999, '999_future', 'x', '2026-10-27T00:00:00.000000Z')"
        ],
    )
    expected = _cost_coefficient(paths.db_path)
    names = _backup_names(paths.backup_dir)

    with pytest.raises(InvalidBackupError, match="新しい版"):
        restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, name)
    _assert_not_restored(paths, expected, names)


def test_restore_rejects_checksum_mismatch(paths: _Paths) -> None:
    name = create_manual_backup(paths.db_path, paths.backup_dir, None)
    _write_raw(
        paths.backup_dir / name,
        ["UPDATE schema_migration SET checksum = 'changed' WHERE version = 1"],
    )
    expected = _cost_coefficient(paths.db_path)
    names = _backup_names(paths.backup_dir)

    with pytest.raises(InvalidBackupError, match="一致しない"):
        restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, name)
    _assert_not_restored(paths, expected, names)


def test_restore_stops_when_safety_backup_fails(
    paths: _Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = create_manual_backup(paths.db_path, paths.backup_dir, None)
    original = _cost_coefficient(paths.db_path)
    _set_cost_coefficient(paths.db_path, original + 1)

    def _fail(*args: object, **kwargs: object) -> Path:
        raise BackupWriteError("バックアップを作成できませんでした")

    monkeypatch.setattr(backup_app, "write_backup", _fail)

    with pytest.raises(BackupCreationError, match="復元していません"):
        restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, name)
    assert _cost_coefficient(paths.db_path) == original + 1


def test_restore_rolls_back_when_log_fails(paths: _Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    """置き換えの後に失敗したら、復元の前の自動のバックアップで元に戻す。"""
    name = create_manual_backup(paths.db_path, paths.backup_dir, None)
    original = _cost_coefficient(paths.db_path)
    _set_cost_coefficient(paths.db_path, original + 1)

    def _fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("記録の失敗")

    monkeypatch.setattr(backup_app, "record_operation_log", _fail)

    with pytest.raises(RestoreFailedError, match="復元の前の状態に戻しました"):
        restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, name)
    assert _cost_coefficient(paths.db_path) == original + 1
    assert _logs(paths.db_path, "復元") == []


def test_restore_reports_when_roll_back_also_fails(
    paths: _Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = create_manual_backup(paths.db_path, paths.backup_dir, None)
    real_restore = infra_backup.restore_database
    calls: list[str] = []

    def _restore_then_fail(backup_path: Path, db_path: Path) -> None:
        calls.append(backup_path.name)
        if len(calls) == 1:
            real_restore(backup_path, db_path)
            return
        raise OSError("元に戻せない")

    def _fail_migration(*args: object, **kwargs: object) -> tuple[str, ...]:
        raise RuntimeError("適用の失敗")

    monkeypatch.setattr(backup_app, "restore_database", _restore_then_fail)
    monkeypatch.setattr(backup_app, "apply_migrations", _fail_migration)

    with pytest.raises(RestoreFailedError, match="戻せませんでした") as excinfo:
        restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, name)
    # 利用者が手で戻せるよう、復元の前のバックアップのファイル名（パスではない）を伝える。
    assert calls[1] in str(excinfo.value)
    assert str(paths.root) not in str(excinfo.value)


def test_restore_rejects_backup_without_passcode(paths: _Paths, tmp_path: Path) -> None:
    """パスコードが設定される前のバックアップは、復元しない（誰でも最初の設定ができてしまうため）。"""
    no_passcode_db = tmp_path / "no_passcode.db"
    with open_connection(no_passcode_db) as connection:
        apply_migrations(connection, _DDL_DIR, no_passcode_db, tmp_path / "other_backups")
    backup = create_backup(no_passcode_db, paths.backup_dir)
    expected = _cost_coefficient(paths.db_path)
    names = _backup_names(paths.backup_dir)

    with pytest.raises(InvalidBackupError, match="パスコードが設定される前"):
        restore_from_backup(paths.db_path, paths.backup_dir, _DDL_DIR, backup.name)

    _assert_not_restored(paths, expected, names)


def test_destination_accepts_unc_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """共有フォルダ（UNC）のパスも、作成先として受け付ける（ユーザー承認 2026-10-28）。"""
    monkeypatch.setattr(Path, "is_dir", lambda _self: True)

    unc = "\\\\server\\share\\backup"

    assert backup_app.parse_backup_destination(unc) == Path(unc)
