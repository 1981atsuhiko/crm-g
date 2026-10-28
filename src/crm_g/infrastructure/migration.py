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
from crm_g.infrastructure.db import open_read_only

_logger = logging.getLogger("crm_g")

# 開発環境でのリポジトリ直下のDDL。配布物でのDDLの同梱は、PH-16で検討する。
BUNDLED_DDL_DIR = Path(__file__).resolve().parents[3] / "DDL"


class InvalidMigrationFilenameError(RuntimeError):
    """連番SQLのファイル名が、命名規則（例：001_create_employee.sql）に合わないときのエラー。"""


class DuplicateMigrationVersionError(RuntimeError):
    """複数のファイルが、同じ版番号（連番）を指しているときのエラー。"""


class ChecksumMismatchError(RuntimeError):
    """適用済みの連番SQLのチェックサムが変わっていたときのエラー。"""


class UnknownMigrationError(RuntimeError):
    """DBのスキーマ版が、アプリの対応版より新しいときのエラー。"""


class InvalidDatabaseFileError(RuntimeError):
    """検査したファイルが、このアプリのDB（`schema_migration` を持つ SQLite）でないときのエラー。"""


class DatabaseNotFoundError(RuntimeError):
    """DBファイルがないときのエラー（アプリを一度も起動していない）。"""


class PendingMigrationError(RuntimeError):
    """DBに、未適用の連番SQLがあるときのエラー（アプリを起動して適用する）。"""


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
        "checksum TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT"
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


def _verify_applied(files: Sequence[MigrationFile], applied: dict[int, tuple[str, str]]) -> None:
    """適用済みの版が同梱のDDLにあり、チェックサムが一致することを確かめる。"""
    file_versions = {file.version for file in files}
    unknown_versions = sorted(set(applied) - file_versions)
    if unknown_versions:
        unknown_names = [applied[version][0] for version in unknown_versions]
        raise UnknownMigrationError(
            "DBに記録された版が、同梱のDDLに見つかりません（アプリより新しいDB、"
            f"またはDDLファイルの削除・リネームの可能性があります）: {unknown_names}"
        )

    _verify_checksums(files, applied)


def _read_applied_read_only(db_path: Path) -> dict[int, tuple[str, str]]:
    """DBファイルを読み取り専用で開き、適用済みの版を読む（書き換えない）。

    SQLite のファイルでない・`schema_migration` がない・版が1つも記録されていないときは、
    `InvalidDatabaseFileError` にする。
    """
    try:
        connection = open_read_only(db_path)
    except sqlite3.Error as error:
        raise InvalidDatabaseFileError("DBファイルを開けません") from error
    try:
        try:
            has_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
            ).fetchone()
            applied = _applied_rows(connection) if has_table is not None else {}
        except sqlite3.Error as error:
            raise InvalidDatabaseFileError("このアプリのDBファイルではありません") from error
    finally:
        connection.close()

    if not applied:
        raise InvalidDatabaseFileError("このアプリのDBファイルではありません")
    return applied


def verify_database_file(db_path: Path, ddl_dir: Path) -> None:
    """DBファイルのスキーマの版を、同梱のDDLと照らして検査する（書き換えない）。

    バックアップからの復元の前の検査に使う（画面設計書 `SCN-065`）。次のとき
    エラーにする。SQLite のファイルでない・`schema_migration` がない・版が1つも
    記録されていない（`InvalidDatabaseFileError`）。同梱のDDLにない版がある
    （`UnknownMigrationError`。アプリより新しい版・未知の版）。適用済みの版の
    チェックサムが合わない（`ChecksumMismatchError`）。同梱のDDLより古い版は、
    エラーにしない（復元の後に、未適用の連番SQLを適用する）。
    """
    files = _list_migration_files(ddl_dir)
    applied = _read_applied_read_only(db_path)
    _verify_applied(files, applied)


def verify_schema_is_current(db_path: Path, ddl_dir: Path) -> None:
    """DBのスキーマが、同梱のDDLの最新の版であることを確かめる（DBを作らず、書き換えない）。

    移行コマンド（移行設計書 `MIG-070`）の前の検査に使う。移行コマンドは、マイグレーションを
    適用しない（アプリの起動時に適用する。ユーザー承認 2026-10-27）。`verify_database_file` の
    エラーに加えて、DBファイルがないとき（`DatabaseNotFoundError`）と、未適用の連番SQLが
    あるとき（`PendingMigrationError`）をエラーにする。
    """
    if not db_path.is_file():
        raise DatabaseNotFoundError("DBファイルがありません")
    files = _list_migration_files(ddl_dir)
    applied = _read_applied_read_only(db_path)
    _verify_applied(files, applied)
    pending = [file.name for file in files if file.version not in applied]
    if pending:
        raise PendingMigrationError(f"未適用の連番SQLがあります: {pending}")


def apply_migrations(
    connection: sqlite3.Connection, ddl_dir: Path, db_path: Path, backup_dir: Path
) -> tuple[str, ...]:
    """未適用の連番SQLを、版番号の順に適用する。適用したファイル名を返す。"""
    _ensure_migration_table(connection)

    files = _list_migration_files(ddl_dir)
    applied = _applied_rows(connection)
    _verify_applied(files, applied)

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
            # 連番SQLが自分で BEGIN したまま失敗すると、トランザクションが開いたまま残る。
            # 同じ接続を使い続けても途中の変更が混ざらないよう、ここで取り消す。
            if connection.in_transaction:
                connection.rollback()
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
