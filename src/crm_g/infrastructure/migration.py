"""マイグレーション（ARC-008）。

スキーマの変更は、`DDL/` 配下の連番SQLで管理する（成果物ルール）。適用済みの版を
`schema_migration` テーブル（版番号・適用日時・SQLのチェックサム）に記録する。
チェックサムは、改行を `\n` に正規化してから計算する（実装計画書 IPD-005）。
起動時に、未適用の連番SQLを順に実行する。適用前に、DBをバックアップする。
"""

import hashlib
import logging
import sqlite3  # noqa: TID251 インフラ層のマイグレーション処理はここに閉じ込める
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from crm_g.infrastructure.backup import create_backup
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now

_logger = logging.getLogger("crm_g")


class ChecksumMismatchError(RuntimeError):
    """適用済みの連番SQLのチェックサムが変わっていたときのエラー。"""


class UnknownMigrationError(RuntimeError):
    """DBのスキーマ版が、アプリの対応版より新しいときのエラー。"""


class MigrationFailedError(RuntimeError):
    """連番SQLの適用に失敗したときのエラー。`backup_path` から復元できる。"""

    def __init__(self, message: str, backup_path: Path) -> None:
        super().__init__(message)
        self.backup_path = backup_path


@dataclass(frozen=True)
class MigrationFile:
    """連番SQLの1ファイル。"""

    version: str
    path: Path


def _list_migration_files(ddl_dir: Path) -> tuple[MigrationFile, ...]:
    """DDLのファイル名の昇順を、版番号の順とする（成果物ルール：連番をゼロ埋めする）。"""
    files = sorted(ddl_dir.glob("*.sql"))
    return tuple(MigrationFile(version=file.stem, path=file) for file in files)


def _checksum(sql_text: str) -> str:
    normalized = sql_text.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration ("
        "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)"
    )
    connection.commit()


def _applied_checksums(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute("SELECT version, checksum FROM schema_migration").fetchall()
    return {version: checksum for version, checksum in rows}


def _verify_checksums(files: Sequence[MigrationFile], applied: dict[str, str]) -> None:
    for file in files:
        if file.version not in applied:
            continue
        checksum = _checksum(file.path.read_text(encoding="utf-8"))
        if checksum != applied[file.version]:
            raise ChecksumMismatchError(f"{file.version} のチェックサムが変わっています")


def apply_migrations(
    connection: sqlite3.Connection, ddl_dir: Path, db_path: Path, backup_dir: Path
) -> tuple[str, ...]:
    """未適用の連番SQLを、版番号の順に適用する。適用した版番号を返す。"""
    _ensure_migration_table(connection)

    files = _list_migration_files(ddl_dir)
    file_versions = {file.version for file in files}

    applied = _applied_checksums(connection)

    unknown_versions = set(applied) - file_versions
    if unknown_versions:
        raise UnknownMigrationError(
            "DBに記録された版が、同梱のDDLに見つかりません（アプリより新しいDB、"
            f"またはDDLファイルの削除・リネームの可能性があります）: {sorted(unknown_versions)}"
        )

    _verify_checksums(files, applied)

    pending = [file for file in files if file.version not in applied]
    if not pending:
        return ()

    backup_path = create_backup(db_path, backup_dir)

    applied_versions: list[str] = []
    for file in pending:
        sql_text = file.path.read_text(encoding="utf-8")
        checksum = _checksum(sql_text)
        try:
            connection.executescript(sql_text)
            connection.execute(
                "INSERT INTO schema_migration (version, applied_at, checksum) VALUES (?, ?, ?)",
                (file.version, to_iso8601(utc_now()), checksum),
            )
            connection.commit()
        except sqlite3.Error as error:
            # ログには版番号・例外の型名だけを残す。SQLの文面・業務データは、
            # これらを含み得るため出さない（ARC-018、SCD-042）。
            _logger.error(
                "マイグレーション %s の適用に失敗しました（%s）",
                file.version,
                type(error).__name__,
            )
            raise MigrationFailedError(
                f"{file.version} の適用に失敗しました。バックアップから復元してください: "
                f"{backup_path}",
                backup_path=backup_path,
            ) from error
        _logger.info("マイグレーション %s を適用しました", file.version)
        applied_versions.append(file.version)

    return tuple(applied_versions)
