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


def test_setup_logging_rotates_when_exceeding_max_bytes(tmp_path: Path) -> None:
    logger = setup_logging(tmp_path, max_bytes=200, backup_count=1)

    for _ in range(20):
        logger.info("ローテーションを発生させるための行です")
    for handler in logger.handlers:
        handler.flush()

    assert (tmp_path / "crm-g.log.1").exists()
