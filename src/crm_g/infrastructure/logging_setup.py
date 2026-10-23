"""アプリケーションログの設定（ARC-018）。

個人情報・給与額・認証情報はログに出さない。必要な場合はマスクする。例外は、
一般的なメッセージに変換して画面に出し、原因はログだけに残す（呼び出し側の責務）。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "crm_g"
_LOG_FILE_NAME = "crm-g.log"

# 容量上限・世代数は暫定値。確定後に見直す（実装計画書11章-1）。
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


def setup_logging(
    log_dir: Path,
    level: int = logging.INFO,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    """アプリのログを、ローテーションするファイルに出力するよう設定する。"""
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    for existing_handler in logger.handlers[:]:
        try:
            existing_handler.close()
        finally:
            logger.removeHandler(existing_handler)
    logger.propagate = False

    handler = RotatingFileHandler(
        log_dir / _LOG_FILE_NAME,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)

    return logger
