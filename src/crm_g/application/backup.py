"""バックアップ・復元のユースケース（画面設計書 `SCN-065`、`ARC-021`）。

- 一覧：既定のバックアップのフォルダ（データ領域の `backups`）にあるファイルを、新しい順に返す
- 作成：既定のフォルダ、または利用者が指定したフォルダに作る（`SCD-040`。14章-16）。
  作成の後、`バックアップ作成` を操作ログに記録する。記録に失敗したら、作ったファイルを消す
- 復元：一覧にあるファイルの名前だけを受け取る（任意のパスを受け取らない。8.6）。
  ①検査（整合性・スキーマの版・チェックサム）→ ②今の DB を自動でバックアップ →
  ③置き換え → ④未適用の連番SQLを適用 → ⑤`復元` を操作ログに記録、の順に行う。
  ③〜⑤のどこかで失敗したら、②のバックアップで元に戻す

操作ログの `detail` には、ファイル名だけを入れる（パスを入れない。`SCD-023`、`ARC-018`）。
例外の文言にもパスを含めない。画面はこの文言をそのまま表示する。
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from crm_g.application.operation_log import Operation, OperationLogDetail, record_operation_log
from crm_g.infrastructure.backup import (
    BackupIntegrityError,
    BackupWriteError,
    delete_backup_file,
    list_backup_files,
    restore_database,
    verify_backup_integrity,
    write_backup,
)
from crm_g.infrastructure.db import open_connection, open_read_only, transaction
from crm_g.infrastructure.migration import (
    ChecksumMismatchError,
    InvalidDatabaseFileError,
    UnknownMigrationError,
    apply_migrations,
    verify_database_file,
)

# 操作ログの `target_table`（必須の列）。DB 全体が対象のため、固定値にする。
BACKUP_TARGET_TABLE = "database"

_logger = logging.getLogger("crm_g")


class BackupDestinationError(ValueError):
    """利用者が指定した作成先が使えないときのエラー（文言にパスを含めない）。"""


class BackupCreationError(RuntimeError):
    """バックアップを作れなかったときのエラー（文言にパスを含めない）。"""


class BackupNotFoundError(LookupError):
    """復元に選んだファイルが、既定のバックアップのフォルダにないときのエラー。"""


class InvalidBackupError(ValueError):
    """復元の前の検査に通らなかったときのエラー。DB は置き換えていない。"""


class RestoreFailedError(RuntimeError):
    """置き換えの後に失敗したときのエラー。復元の前の自動のバックアップで元に戻している。"""


@dataclass(frozen=True)
class BackupEntry:
    """バックアップの一覧の1行。`created_at` はタイムゾーン付き（UTC）。"""

    name: str
    created_at: datetime
    size_bytes: int


@dataclass(frozen=True)
class RestoreResult:
    """復元の結果。`safety_backup_name` は、復元の前に自動で作ったバックアップのファイル名。"""

    restored_name: str
    safety_backup_name: str
    applied_migrations: tuple[str, ...]


def list_backups(backup_dir: Path) -> tuple[BackupEntry, ...]:
    """既定のバックアップのフォルダにあるバックアップを、新しい順に返す。"""
    return tuple(
        BackupEntry(name=file.name, created_at=file.created_at, size_bytes=file.size_bytes)
        for file in list_backup_files(backup_dir)
    )


def parse_backup_destination(text: str) -> Path | None:
    """利用者が入力した作成先のフォルダを検証する。空欄のときは `None`（既定のフォルダ）。

    前後の空白と、前後を囲む `"`（エクスプローラーの「パスのコピー」で付く）を除く。
    絶対パスで、既にあるフォルダだけを受け付ける。フォルダは作らない（入力の誤りで、
    意図しない場所にフォルダができないようにするため）。書き込めるかは、作成のときに
    確かめる。エラーの文言には、入力したパスを含めない（`ARC-018`）。
    """
    stripped = text.strip()
    if len(stripped) >= 2 and stripped.startswith('"') and stripped.endswith('"'):
        stripped = stripped[1:-1].strip()
    if not stripped:
        return None
    if "\x00" in stripped:
        raise BackupDestinationError("作成先のフォルダのパスに、使えない文字が含まれています")
    destination = Path(stripped)
    if not destination.is_absolute():
        raise BackupDestinationError(
            "作成先は、ドライブ名（例：D:\\backup）または共有フォルダ（例：\\\\サーバー\\共有）"
            "から始まるフォルダのパスで指定してください"
        )
    if not destination.is_dir():
        raise BackupDestinationError("作成先のフォルダが見つかりません")
    return destination


def create_manual_backup(db_path: Path, backup_dir: Path, destination: Path | None) -> str:
    """手動のバックアップを作り、`バックアップ作成` を記録する。作ったファイル名を返す。

    `destination` が `None` のときは、既定のフォルダ（`backup_dir`。なければ作る）に作る。
    """
    try:
        if destination is None:
            backup_path = write_backup(db_path, backup_dir, create_directory=True)
        else:
            backup_path = write_backup(db_path, destination, create_directory=False)
    except BackupWriteError as error:
        if destination is None:
            raise BackupCreationError("バックアップを作成できませんでした") from error
        raise BackupDestinationError(
            "作成先のフォルダに書き込めません。フォルダとアクセス権を確認してください"
        ) from error

    try:
        with open_connection(db_path) as connection, transaction(connection):
            record_operation_log(
                connection,
                Operation.BACKUP_CREATE,
                BACKUP_TARGET_TABLE,
                OperationLogDetail(detail=backup_path.name),
            )
    except BaseException:
        # 記録のない操作を残さない（`SCD-021`）。作ったファイルを消してから伝える。
        delete_backup_file(backup_path)
        raise
    return backup_path.name


def _find_backup(backup_dir: Path, file_name: str) -> Path:
    """一覧にあるファイルの名前から、パスを求める。一覧にない名前は受け付けない。"""
    for file in list_backup_files(backup_dir):
        if file.name == file_name:
            return file.path
    raise BackupNotFoundError("選んだバックアップが見つかりません。一覧を確認してください")


def _verify_backup(backup_path: Path, ddl_dir: Path) -> None:
    """復元の前の検査（整合性・スキーマの版・チェックサム）。失敗は `InvalidBackupError`。"""
    try:
        verify_backup_integrity(backup_path)
    except BackupIntegrityError as error:
        raise InvalidBackupError(
            "バックアップのファイルが、整合性の検査に通りませんでした。復元していません"
        ) from error
    try:
        verify_database_file(backup_path, ddl_dir)
    except InvalidDatabaseFileError as error:
        raise InvalidBackupError(
            "このアプリのバックアップのファイルではありません。復元していません"
        ) from error
    except UnknownMigrationError as error:
        raise InvalidBackupError(
            "このアプリより新しい版、または不明な版のバックアップのため、復元できません"
        ) from error
    except ChecksumMismatchError as error:
        raise InvalidBackupError(
            "バックアップのスキーマが、このアプリの版と一致しないため、復元できません"
        ) from error
    if not _has_passcode(backup_path):
        # 復元するとパスコードも戻る。未設定の状態に戻すと、誰でも最初の設定が
        # できてしまうため、復元しない（権限・セキュリティ設計書 `SCD-011`）。
        raise InvalidBackupError("パスコードが設定される前のバックアップのため、復元できません")


def _has_passcode(backup_path: Path) -> bool:
    """バックアップに、パスコードが設定されているか。"""
    connection = open_read_only(backup_path)
    try:
        return (
            connection.execute("SELECT 1 FROM app_credential WHERE id = 1").fetchone() is not None
        )
    finally:
        connection.close()


def restore_from_backup(
    db_path: Path, backup_dir: Path, ddl_dir: Path, file_name: str
) -> RestoreResult:
    """一覧から選んだバックアップで、DB を置き換える（`SCN-065`、`TC-SCN-021`）。

    検査に失敗したときは、置き換えずに `InvalidBackupError`。置き換えの後に失敗した
    ときは、復元の前の自動のバックアップで元に戻し、`RestoreFailedError`。
    """
    backup_path = _find_backup(backup_dir, file_name)
    _verify_backup(backup_path, ddl_dir)

    try:
        safety_path = write_backup(db_path, backup_dir, create_directory=True)
    except BackupWriteError as error:
        raise BackupCreationError(
            "復元の前のバックアップを作成できませんでした。復元していません"
        ) from error

    try:
        restore_database(backup_path, db_path)
        with open_connection(db_path) as connection:
            # 古い版のバックアップには、未適用の連番SQLを適用する（復元の記録は、
            # 適用の後に追記する。`復元` の検査値は DDL/024 で加えたため）。
            applied = apply_migrations(connection, ddl_dir, db_path, backup_dir)
            with transaction(connection):
                record_operation_log(
                    connection,
                    Operation.RESTORE,
                    BACKUP_TARGET_TABLE,
                    OperationLogDetail(detail=backup_path.name),
                )
    except Exception as error:
        _logger.error("復元に失敗しました（%s）。元に戻します", type(error).__name__)
        if _roll_back(safety_path, db_path):
            message = "復元に失敗しました。復元の前の状態に戻しました。"
        else:
            message = (
                "復元に失敗し、復元の前の状態にも戻せませんでした。"
                f"復元の前のバックアップ（{safety_path.name}）から、もう一度復元してください。"
            )
        raise RestoreFailedError(message) from error

    return RestoreResult(
        restored_name=backup_path.name,
        safety_backup_name=safety_path.name,
        applied_migrations=applied,
    )


def _roll_back(safety_path: Path, db_path: Path) -> bool:
    """復元の前の自動のバックアップで、DB を元に戻す。戻せたら `True`。"""
    try:
        restore_database(safety_path, db_path)
    except Exception as error:
        _logger.critical(
            "復元の失敗の後、元に戻せませんでした（%s）。バックアップ %s から戻してください",
            type(error).__name__,
            safety_path.name,
        )
        return False
    return True
