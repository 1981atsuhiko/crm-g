"""起動用のエントリポイント（`ARC-014`）。

多重起動の防止・DBのマイグレーション適用・固定ポートの空き確認を行ってから、
`streamlit.web.bootstrap` でStreamlitを起動し、標準ライブラリ `webbrowser` で
ブラウザを開く（実装計画書 4.5。`POC-08` で実証済みの方式）。
"""

import logging
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from streamlit.web import bootstrap  # noqa: TID251 起動用launcherだけの例外（ARC-014。POC-08で実証済み）

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.logging_setup import setup_logging
from crm_g.infrastructure.migration import BUNDLED_DDL_DIR, apply_migrations
from crm_g.infrastructure.paths import get_app_paths
from crm_g.infrastructure.single_instance import AlreadyRunningError, acquire_lock

_HOST = "127.0.0.1"
_PORT = 8501
_LOCK_FILE_NAME = "crm-g.lock"

_UI_APP_PATH = Path(__file__).resolve().parent.parent / "ui" / "app.py"

_BROWSER_OPEN_TIMEOUT_SECONDS = 10.0
_BROWSER_OPEN_POLL_INTERVAL_SECONDS = 0.1

_logger = logging.getLogger("crm_g")


def _is_port_available(host: str, port: int) -> bool:
    """ポートの空きを確認する（簡易な事前確認であり、確保を保証しない）。

    確認と実際の起動の間には、わずかな競合の余地がある（単一利用者の
    デスクトップアプリのため、許容する）。起動自体が失敗した場合は、
    `serve()` の例外処理で検知し、エラーを表示して終了する。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _wait_for_server_and_open_browser(host: str, port: int) -> None:
    """サーバーが起動するまで待ってから、ブラウザを開く。開けない場合は、URLを表示する。"""
    url = f"http://{host}:{port}"
    deadline = time.monotonic() + _BROWSER_OPEN_TIMEOUT_SECONDS
    connected = False
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(_BROWSER_OPEN_POLL_INTERVAL_SECONDS)
            try:
                sock.connect((host, port))
            except OSError:
                time.sleep(_BROWSER_OPEN_POLL_INTERVAL_SECONDS)
                continue
        connected = True
        break

    if not connected:
        print(f"起動の確認がとれませんでした。次のURLを開いてください: {url}")
        return

    if not webbrowser.open(url):
        print(f"ブラウザを自動で開けませんでした。次のURLを開いてください: {url}")


def _build_flag_options() -> dict[str, str | int | bool | list[str]]:
    """Streamlitの起動オプション。

    `.streamlit/config.toml` の発見可否（起動時のカレントディレクトリに依存する）に
    左右されないよう、セキュリティに関わる設定は、ここでも明示的に指定する
    （`ARC-016`、`ARC-018`）。
    """
    return {
        "server_headless": True,
        "server_address": _HOST,
        "server_port": _PORT,
        "server_fileWatcherType": "none",
        "server_enableXsrfProtection": True,
        "server_enableCORS": True,
        "server_allowedHosts": ["localhost", "127.0.0.1"],
        "browser_gatherUsageStats": False,
        "client_showErrorDetails": "none",
        "global_developmentMode": False,
    }


def serve() -> int:
    """アプリを起動する。失敗したときは、エラーメッセージを表示して終了コードを返す。"""
    paths = get_app_paths()
    setup_logging(paths.log_dir)

    try:
        with acquire_lock(paths.data_dir / _LOCK_FILE_NAME):
            if not _is_port_available(_HOST, _PORT):
                print(
                    f"ポート {_PORT} は使用中です。他のアプリを終了してから、やり直してください。"
                )
                return 1

            with open_connection(paths.db_path) as connection:
                apply_migrations(connection, BUNDLED_DDL_DIR, paths.db_path, paths.backup_dir)

            flag_options = _build_flag_options()
            bootstrap.load_config_options(flag_options=flag_options)

            threading.Thread(
                target=_wait_for_server_and_open_browser, args=(_HOST, _PORT), daemon=True
            ).start()
            bootstrap.run(str(_UI_APP_PATH), False, [], flag_options)
            return 0
    except AlreadyRunningError:
        print("既に起動しています。")
        return 1
    except Exception:
        _logger.exception("起動に失敗しました")
        print("起動に失敗しました。ログを確認してください。")
        return 1


def main() -> int:
    return serve()


if __name__ == "__main__":
    sys.exit(main())
