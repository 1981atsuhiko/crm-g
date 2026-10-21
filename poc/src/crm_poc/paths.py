"""exe 化の有無によらずリソースの場所を解決する（POC-01）。"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_NAME = "crm_poc"
EXE_UI_DIRECTORY_NAME = "exe_ui"
RESOURCES_DIRECTORY_NAME = "resources"
SCHEMA_FILE_NAME = "schema.sql"
EXE_APP_FILE_NAME = "app.py"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """exe 化時は同梱ファイルの展開先（PyInstaller）または exe のあるフォルダー（cx_Freeze）。"""
    bundle_directory = getattr(sys, "_MEIPASS", None)
    if bundle_directory is not None:
        return Path(str(bundle_directory))
    return Path(sys.executable).parent


def package_directory() -> Path:
    if is_frozen():
        return app_root() / PACKAGE_NAME
    return Path(__file__).resolve().parent


def exe_ui_directory() -> Path:
    return package_directory() / EXE_UI_DIRECTORY_NAME


def schema_path() -> Path:
    return package_directory() / RESOURCES_DIRECTORY_NAME / SCHEMA_FILE_NAME
