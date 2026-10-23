"""設定ファイルの読み込みの試験（ARC-006）。"""

from pathlib import Path

import pytest

from crm_g.infrastructure.config import load_toml_file


def test_load_toml_file_reads_table(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[section]\nkey = "value"\n', encoding="utf-8")

    result = load_toml_file(config_path)

    assert result == {"section": {"key": "value"}}


def test_load_toml_file_raises_when_file_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_toml_file(tmp_path / "missing.toml")
