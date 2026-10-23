"""tests/integration 共通のフィクスチャ。"""

import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _cleanup_crm_g_logger() -> Iterator[None]:
    """setup_logging が書き換える crm_g ロガーのハンドラを、試験後に後始末する。

    ハンドラを閉じないまま残すと、Windows で一時ディレクトリのファイルハンドルが
    残り続け、他の試験や一時ディレクトリの削除に影響する（コーディング規約：
    試験は独立して実行でき、順序や外部状態に依存しない）。
    """
    yield

    logger = logging.getLogger("crm_g")
    for handler in logger.handlers[:]:
        try:
            handler.close()
        finally:
            logger.removeHandler(handler)
