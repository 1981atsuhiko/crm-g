"""バックアップ・復元（ARC-021）。

標準 `sqlite3` の接続が持つバックアップ機能（`Connection.backup`）で作成する。
作成時点の整合性を保つため。ファイル名は `crm-g_YYYYMMDD_HHMMSS.db`
（画面設計書）。
"""

import shutil
import sqlite3  # noqa: TID251 インフラ層のバックアップ処理はここに閉じ込める
from datetime import UTC, datetime
from pathlib import Path

from crm_g.infrastructure.datetime_utils import utc_now

_BACKUP_FILENAME_FORMAT = "crm-g_%Y%m%d_%H%M%S.db"


def _unique_backup_path(backup_dir: Path, now: datetime) -> Path:
    """同じ秒に複数回バックアップしても、既存のファイルを上書きしないパスを作る。"""
    base_name = now.strftime(_BACKUP_FILENAME_FORMAT)
    candidate = backup_dir / base_name
    suffix = 1
    while candidate.exists():
        candidate = backup_dir / f"{Path(base_name).stem}_{suffix}.db"
        suffix += 1
    return candidate


def create_backup(db_path: Path, backup_dir: Path, now: datetime | None = None) -> Path:
    """DBファイルのバックアップを作成し、作成したファイルのパスを返す。"""
    if now is None:
        now = utc_now()
    elif now.tzinfo is None:
        raise ValueError("now はタイムゾーン付きの datetime にしてください")
    else:
        now = now.astimezone(UTC)

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = _unique_backup_path(backup_dir, now)

    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    return backup_path


def restore_backup(backup_path: Path, db_path: Path) -> None:
    """バックアップファイルを、DBファイルの場所に複写して復元する。"""
    shutil.copy2(backup_path, db_path)
