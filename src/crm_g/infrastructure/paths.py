"""データ領域のパス（ARC-006）。

設定ファイル・DBファイル・ログファイル・バックアップは、OS標準のユーザーデータ領域に
置く。パスは設定モジュールで一元管理し、コードにハードコードしない（ARC-006）。
OS固有の処理は、インフラ層のこの1か所に閉じ込める（実装計画書 IPL-025）。
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

_APP_NAME = "crm-g"


class UnsupportedPlatformError(RuntimeError):
    """対応していないOS、またはOSの必要な環境変数が無いときのエラー。"""


@dataclass(frozen=True)
class AppPaths:
    """アプリのデータ領域のパス。"""

    data_dir: Path
    db_path: Path
    log_dir: Path
    backup_dir: Path
    config_path: Path


def get_app_paths() -> AppPaths:
    """OSに応じたデータ領域のパスを求める（実装計画書 IPD-009）。"""
    data_dir = _resolve_data_dir()
    return AppPaths(
        data_dir=data_dir,
        db_path=data_dir / "crm-g.db",
        log_dir=data_dir / "logs",
        backup_dir=data_dir / "backups",
        config_path=data_dir / "config.toml",
    )


def _resolve_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise UnsupportedPlatformError("環境変数 LOCALAPPDATA が設定されていません")
        return Path(base) / _APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_NAME

    raise UnsupportedPlatformError(f"対応していないOSです: {sys.platform}")
