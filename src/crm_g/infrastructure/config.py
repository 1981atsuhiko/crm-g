"""設定ファイルの読み込み（ARC-006）。

設定ファイルは TOML とし、秘密情報を平文で置かない。具体的な設定項目は、
各フェーズで必要になった段階で追加する（現時点では確定した項目がない）。
"""

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import cast


def load_toml_file(path: Path) -> Mapping[str, object]:
    """TOML設定ファイルを読み込む。"""
    with path.open("rb") as file:
        return cast(Mapping[str, object], tomllib.load(file))
