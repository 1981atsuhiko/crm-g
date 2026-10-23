"""層の依存禁止を ruff の TID251 で検査する試験（TC-ARC-001、TC-ARC-002）。"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_ruff_tid251(target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "TID251", target],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_tc_arc_001_domain_layer_has_no_banned_imports() -> None:
    """ドメイン層は sqlite3・streamlit・jpholiday・csv を import していない。"""
    result = _run_ruff_tid251("src/crm_g/domain")

    assert result.returncode == 0, result.stdout


def test_tc_arc_002_application_and_cli_have_no_banned_imports() -> None:
    """アプリケーション層・cli は UI 層モジュールと streamlit を import していない。"""
    result = _run_ruff_tid251("src/crm_g/application")
    assert result.returncode == 0, result.stdout

    result = _run_ruff_tid251("src/crm_g/cli")
    assert result.returncode == 0, result.stdout
