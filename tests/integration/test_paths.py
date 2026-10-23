"""データ領域のパスの試験（ARC-006）。"""

import sys
from pathlib import Path

import pytest

from crm_g.infrastructure.paths import UnsupportedPlatformError, get_app_paths


def test_get_app_paths_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    paths = get_app_paths()

    assert paths.data_dir == tmp_path / "crm-g"
    assert paths.db_path == tmp_path / "crm-g" / "crm-g.db"
    assert paths.log_dir == tmp_path / "crm-g" / "logs"
    assert paths.backup_dir == tmp_path / "crm-g" / "backups"
    assert paths.config_path == tmp_path / "crm-g" / "config.toml"


def test_get_app_paths_on_windows_without_localappdata_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    with pytest.raises(UnsupportedPlatformError, match="LOCALAPPDATA"):
        get_app_paths()


def test_get_app_paths_on_macos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    paths = get_app_paths()

    assert paths.data_dir == tmp_path / "Library" / "Application Support" / "crm-g"


def test_get_app_paths_on_unsupported_os_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(UnsupportedPlatformError, match="対応していないOS"):
        get_app_paths()
