"""アプリケーションログの設定の試験（ARC-018）。"""

import logging
from pathlib import Path

from crm_g.infrastructure.logging_setup import setup_logging


def test_setup_logging_writes_to_file(tmp_path: Path) -> None:
    logger = setup_logging(tmp_path)

    logger.info("テストメッセージ")
    for handler in logger.handlers:
        handler.flush()

    log_file = tmp_path / "crm-g.log"
    assert log_file.exists()
    assert "テストメッセージ" in log_file.read_text(encoding="utf-8")


def test_setup_logging_creates_log_directory(tmp_path: Path) -> None:
    log_dir = tmp_path / "nested" / "logs"

    setup_logging(log_dir)

    assert log_dir.is_dir()


def test_setup_logging_respects_level(tmp_path: Path) -> None:
    logger = setup_logging(tmp_path, level=logging.WARNING)

    logger.info("この行は出力されない")
    logger.warning("この行は出力される")
    for handler in logger.handlers:
        handler.flush()

    content = (tmp_path / "crm-g.log").read_text(encoding="utf-8")
    assert "この行は出力されない" not in content
    assert "この行は出力される" in content


def test_tc_arc_022_log_rotates_and_total_size_stays_within_limit(tmp_path: Path) -> None:
    """ログの上限を小さくして、上限を超えるまで出力する（TC-ARC-022）。

    ログが次のファイルに切り替わり、総量が上限（ファイルの数×1ファイルの上限）
    以下になることを確認する。
    """
    max_bytes = 200
    backup_count = 1
    logger = setup_logging(tmp_path, max_bytes=max_bytes, backup_count=backup_count)

    for _ in range(20):
        logger.info("ローテーションを発生させるための行です")
    for handler in logger.handlers:
        handler.flush()

    assert (tmp_path / "crm-g.log.1").exists()

    total_size = sum(path.stat().st_size for path in tmp_path.glob("crm-g.log*") if path.is_file())
    assert total_size <= max_bytes * (backup_count + 1)
