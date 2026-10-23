"""マイグレーション（ARC-008）。

スキーマの変更は、`DDL/` 配下の連番SQLで管理する（成果物ルール）。適用済みの版を
`schema_migration` テーブル（データ設計書 TBL-021：版番号・ファイル名・適用日時・
SQLのチェックサム）に記録する。チェックサムは、改行を `\n` に正規化してから
計算する（実装計画書 IPD-005）。起動時に、未適用の連番SQLを順に実行する。
適用前に、DBをバックアップする。

`schema_migration` 自身は、DDL（連番SQL）の適用対象ではなく、ここで直接
作成する（version の記録先そのものを、連番SQLで作ることはできないため）。
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


class InvalidMigrationFilenameError(RuntimeError):
    """連番SQLのファイル名が、命名規則（例：001_create_employee.sql）に合わないときのエラー。"""


class DuplicateMigrationVersionError(RuntimeError):
    """複数のファイルが、同じ版番号（連番）を指しているときのエラー。"""


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
    """連番SQLの1ファイル。`version` は、ファイル名先頭の連番（整数）。"""

    version: int
    name: str
    path: Path


def _parse_version(stem: str) -> int:
    prefix = stem.split("_", 1)[0]
    try:
        return int(prefix)
    except ValueError as error:
        raise InvalidMigrationFilenameError(
            f"{stem} は連番SQLの命名規則（例：001_create_employee）に合いません"
        ) from error


def _list_migration_files(ddl_dir: Path) -> tuple[MigrationFile, ...]:
    """DDLのファイルを、版番号（ファイル名先頭の連番、整数）の昇順に並べる。

    ファイル名の文字列としての順ではなく、整数に変換した版番号で並べる。
    ゼロ埋めの桁数が揃っていないファイル（例：`2_x.sql` と `10_y.sql`）が
    混在しても、適用順が崩れないようにするため。
    """
    unsorted_files = [
        MigrationFile(version=_parse_version(file.stem), name=file.stem, path=file)
        for file in ddl_dir.glob("*.sql")
    ]
    files = tuple(sorted(unsorted_files, key=lambda file: file.version))

    version_counts: dict[int, int] = {}
    for file in files:
        version_counts[file.version] = version_counts.get(file.version, 0) + 1
    duplicated = sorted(version for version, count in version_counts.items() if count > 1)
    if duplicated:
        raise DuplicateMigrationVersionError(
            f"版番号が重複しているファイルがあります: {duplicated}"
        )

    return files


def _checksum(sql_text: str) -> str:
    normalized = sql_text.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
        "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    connection.commit()


def _applied_rows(connection: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    """適用済みの版番号から、(ファイル名, チェックサム) への対応を返す。"""
    rows = connection.execute("SELECT version, name, checksum FROM schema_migration").fetchall()
    return {version: (name, checksum) for version, name, checksum in rows}


def _verify_checksums(files: Sequence[MigrationFile], applied: dict[int, tuple[str, str]]) -> None:
    for file in files:
        if file.version not in applied:
            continue
        _, applied_checksum = applied[file.version]
        checksum = _checksum(file.path.read_text(encoding="utf-8"))
        if checksum != applied_checksum:
            raise ChecksumMismatchError(f"{file.name} のチェックサムが変わっています")


def apply_migrations(
    connection: sqlite3.Connection, ddl_dir: Path, db_path: Path, backup_dir: Path
) -> tuple[str, ...]:
    """未適用の連番SQLを、版番号の順に適用する。適用したファイル名を返す。"""
    _ensure_migration_table(connection)

    files = _list_migration_files(ddl_dir)
    file_versions = {file.version for file in files}

    applied = _applied_rows(connection)

    unknown_versions = sorted(set(applied) - file_versions)
    if unknown_versions:
        unknown_names = [applied[version][0] for version in unknown_versions]
        raise UnknownMigrationError(
            "DBに記録された版が、同梱のDDLに見つかりません（アプリより新しいDB、"
            f"またはDDLファイルの削除・リネームの可能性があります）: {unknown_names}"
        )

    _verify_checksums(files, applied)

    pending = [file for file in files if file.version not in applied]
    if not pending:
        return ()

    backup_path = create_backup(db_path, backup_dir)

    applied_names: list[str] = []
    for file in pending:
        sql_text = file.path.read_text(encoding="utf-8")
        checksum = _checksum(sql_text)
        try:
            connection.executescript(sql_text)
            connection.execute(
                "INSERT INTO schema_migration (version, name, checksum, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (file.version, file.name, checksum, to_iso8601(utc_now())),
            )
            connection.commit()
        except sqlite3.Error as error:
            # ログにはファイル名・例外の型名だけを残す。SQLの文面・業務データは、
            # これらを含み得るため出さない（ARC-018、SCD-042）。
            _logger.error(
                "マイグレーション %s の適用に失敗しました（%s）",
                file.name,
                type(error).__name__,
            )
            raise MigrationFailedError(
                f"{file.name} の適用に失敗しました。バックアップから復元してください: "
                f"{backup_path}",
                backup_path=backup_path,
            ) from error
        _logger.info("マイグレーション %s を適用しました", file.name)
        applied_names.append(file.name)

    return tuple(applied_names)
