"""バックアップ・復元（ARC-021）。

標準 `sqlite3` の接続が持つバックアップ機能（`Connection.backup`）で作成する。
作成時点の整合性を保つため。ファイル名は `crm-g_YYYYMMDD_HHMMSS.db`
（画面設計書）。日時は UTC。同じ秒に複数回作るときは、末尾に `_1`、`_2`… を付ける。

画面からの復元（画面設計書 `SCN-065`）は、`restore_database` で行う。バックアップの
ファイルを読み取り専用で開き、`Connection.backup` で本体の DB へ書き込む。ファイルの
複写による上書き（`restore_backup`）と異なり、他のタブの接続と SQLite のロックで
調停され、ジャーナルのファイルも残らない。
"""

import logging
import re
import shutil
import sqlite3  # noqa: TID251 インフラ層のバックアップ処理はここに閉じ込める
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from crm_g.infrastructure.datetime_utils import utc_now
from crm_g.infrastructure.db import open_read_only

_BACKUP_FILENAME_FORMAT = "crm-g_%Y%m%d_%H%M%S.db"
# バックアップのファイル名（`crm-g_YYYYMMDD_HHMMSS.db`、同じ秒の2つ目以降は `_n` 付き）。
_BACKUP_FILENAME_PATTERN = re.compile(r"crm-g_(?P<stamp>\d{8}_\d{6})(?:_(?P<suffix>\d+))?\.db")
_BACKUP_STAMP_FORMAT = "%Y%m%d_%H%M%S"

_logger = logging.getLogger("crm_g")


class BackupWriteError(RuntimeError):
    """バックアップのファイルを作れなかったときのエラー（作成先に書き込めないなど）。"""


class BackupIntegrityError(RuntimeError):
    """バックアップのファイルが、整合性の検査（`PRAGMA integrity_check`）に通らないときのエラー。

    SQLite のファイルでない・壊れている・開けないときも、このエラーにする。
    """


@dataclass(frozen=True)
class BackupFile:
    """バックアップのフォルダにある1ファイル。`created_at` はファイル名の日時（UTC）。"""

    name: str
    path: Path
    created_at: datetime
    size_bytes: int


def _unique_backup_path(backup_dir: Path, now: datetime) -> Path:
    """同じ秒に複数回バックアップしても、既存のファイルを上書きしないパスを作る。"""
    base_name = now.strftime(_BACKUP_FILENAME_FORMAT)
    candidate = backup_dir / base_name
    suffix = 1
    while candidate.exists():
        candidate = backup_dir / f"{Path(base_name).stem}_{suffix}.db"
        suffix += 1
    return candidate


def _normalize_now(now: datetime | None) -> datetime:
    """作成日時を UTC にそろえる。ナイーブな datetime は受け付けない。"""
    if now is None:
        return utc_now()
    if now.tzinfo is None:
        raise ValueError("now はタイムゾーン付きの datetime にしてください")
    return now.astimezone(UTC)


def _copy_database(db_path: Path, backup_path: Path) -> None:
    """`Connection.backup` で、DBの内容を `backup_path` のファイルに書き出す。"""
    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def create_backup(db_path: Path, backup_dir: Path, now: datetime | None = None) -> Path:
    """DBファイルのバックアップを作成し、作成したファイルのパスを返す。"""
    now = _normalize_now(now)

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = _unique_backup_path(backup_dir, now)
    _copy_database(db_path, backup_path)
    return backup_path


def write_backup(
    db_path: Path,
    directory: Path,
    *,
    create_directory: bool,
    now: datetime | None = None,
) -> Path:
    """画面からの手動のバックアップを作り、作成したファイルのパスを返す（`SCN-065`）。

    `create_directory` が `False` のとき（利用者が指定したフォルダ）は、フォルダを
    作らない。フォルダがない・書き込めないときは `BackupWriteError`。例外の文言・
    ログにパスを含めない（`ARC-018`）。作りかけのファイルは消す。
    """
    now = _normalize_now(now)
    if not create_directory and not directory.is_dir():
        raise BackupWriteError("作成先のフォルダが見つかりません")
    backup_path: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        backup_path = _unique_backup_path(directory, now)
        _copy_database(db_path, backup_path)
    except (OSError, sqlite3.Error) as error:
        _logger.error("バックアップの作成に失敗しました（%s）", type(error).__name__)
        if backup_path is not None:
            _remove_quietly(backup_path)
        raise BackupWriteError("バックアップを作成できませんでした") from error
    return backup_path


def _remove_quietly(path: Path) -> None:
    """作りかけのファイルを消す。消せなくても、元のエラーを優先して続ける。"""
    try:
        delete_backup_file(path)
    except OSError:
        _logger.warning("作りかけのバックアップのファイルを消せませんでした")


def delete_backup_file(backup_path: Path) -> None:
    """作成したバックアップのファイルを消す（操作ログの記録に失敗したときの後始末）。"""
    backup_path.unlink(missing_ok=True)


def _parse_backup_name(name: str) -> tuple[datetime, int] | None:
    """ファイル名から、作成日時（UTC）と同じ秒の通し番号を取り出す。形式が違えば `None`。"""
    matched = _BACKUP_FILENAME_PATTERN.fullmatch(name)
    if matched is None:
        return None
    try:
        created_at = datetime.strptime(matched["stamp"], _BACKUP_STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
    suffix = int(matched["suffix"]) if matched["suffix"] is not None else 0
    return created_at, suffix


def list_backup_files(backup_dir: Path) -> tuple[BackupFile, ...]:
    """バックアップのフォルダにあるファイルを、新しい順に返す。

    ファイル名が `crm-g_YYYYMMDD_HHMMSS[_n].db` のファイルだけを対象にする。
    フォルダがないときは、空を返す。
    """
    if not backup_dir.is_dir():
        return ()
    entries: list[tuple[datetime, int, BackupFile]] = []
    for path in backup_dir.iterdir():
        parsed = _parse_backup_name(path.name)
        if parsed is None or not path.is_file():
            continue
        created_at, suffix = parsed
        entries.append(
            (
                created_at,
                suffix,
                BackupFile(
                    name=path.name,
                    path=path,
                    created_at=created_at,
                    size_bytes=path.stat().st_size,
                ),
            )
        )
    entries.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return tuple(entry[2] for entry in entries)


def verify_backup_integrity(backup_path: Path) -> None:
    """バックアップのファイルを読み取り専用で開き、`PRAGMA integrity_check` で検査する。"""
    try:
        connection = open_read_only(backup_path)
    except sqlite3.Error as error:
        raise BackupIntegrityError("バックアップのファイルを開けません") from error
    try:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as error:
        raise BackupIntegrityError("バックアップのファイルを検査できません") from error
    finally:
        connection.close()
    if rows != [("ok",)]:
        raise BackupIntegrityError("バックアップのファイルが、整合性の検査に通りません")


def restore_database(backup_path: Path, db_path: Path) -> None:
    """バックアップの内容で、DBファイルの中身を置き換える（`SCN-065`）。

    バックアップのファイルは読み取り専用で開き、書き換えない。本体の DB への書き込みは
    `Connection.backup` で行う。他のタブの接続が書き込み中のときは、終わるまで待つ。
    """
    source = open_read_only(backup_path)
    try:
        destination = sqlite3.connect(db_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    _logger.info("バックアップ %s から復元しました", backup_path.name)


def restore_backup(backup_path: Path, db_path: Path) -> None:
    """バックアップファイルを、DBファイルの場所に複写して復元する。

    起動前（DB を誰も開いていないとき）の手作業の復元用。画面からの復元には
    `restore_database` を使う。
    """
    shutil.copy2(backup_path, db_path)
