"""cx_Freeze 版のビルド設定（POC-01）。

poc/ 直下で `python packaging\\setup_cx_freeze.py build_exe` を実行する。
"""

import sys
from pathlib import Path

from cx_Freeze import Executable, setup

POC_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = POC_ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "crm_poc"
BUILD_DIRECTORY = POC_ROOT / "dist" / "cx_freeze"

build_options = {
    "build_exe": str(BUILD_DIRECTORY),
    "path": [str(SOURCE_ROOT), *sys.path],
    "packages": ["streamlit", "jpholiday", "crm_poc"],
    "include_files": [
        (str(PACKAGE_ROOT / "exe_ui"), "crm_poc/exe_ui"),
        (str(PACKAGE_ROOT / "resources"), "crm_poc/resources"),
    ],
    "include_msvcr": True,
}

setup(
    name="crm_poc",
    version="0.0.0",
    options={"build_exe": build_options},
    executables=[Executable(str(PACKAGE_ROOT / "launcher.py"), target_name="crm_poc_cx.exe")],
)
