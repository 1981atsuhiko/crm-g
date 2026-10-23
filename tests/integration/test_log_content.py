"""アプリのログファイルの内容の試験（TC-SCD-015）。

現時点では、パスコード・給与額・個人情報が実際に流れる経路（認証はPH-05、
給与はPH-08以降）が未実装のため、ここではマイグレーションのログに合成の
マーカーが出ないことだけを確認する。各フェーズで実データの経路を実装する際も、
同様の検証（ログファイルに機微情報が含まれないこと）を継続すること。
"""

import logging
import sqlite3
from pathlib import Path

from crm_g.infrastructure.logging_setup import setup_logging
from crm_g.infrastructure.migration import apply_migrations

_SENSITIVE_MARKERS = (
    "パスコード",
    "password",
    "passcode",
    "給与",
    "salary",
    "500000",
)


def test_tc_scd_015_migration_log_contains_no_sensitive_values(tmp_path: Path) -> None:
    """マイグレーションを実行したログファイルに、個人情報・給与額・認証情報が含まれない。"""
    setup_logging(tmp_path)

    ddl_dir = tmp_path / "ddl"
    ddl_dir.mkdir()
    (ddl_dir / "001_create_a.sql").write_text(
        "CREATE TABLE a (id INTEGER PRIMARY KEY, amount INTEGER);"
        "INSERT INTO a (id, amount) VALUES (1, 500000);",
        encoding="utf-8",
    )
    db_path = tmp_path / "test.db"
    connection = sqlite3.connect(db_path)

    try:
        apply_migrations(connection, ddl_dir, db_path, tmp_path / "backups")
    finally:
        connection.close()
        for handler in logging.getLogger("crm_g").handlers[:]:
            handler.flush()

    log_text = (tmp_path / "crm-g.log").read_text(encoding="utf-8")

    assert "001_create_a" in log_text
    for marker in _SENSITIVE_MARKERS:
        assert marker not in log_text
