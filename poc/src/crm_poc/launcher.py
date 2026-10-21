"""exe の入口。サブコマンドで POC-01・02・06 の確認と Streamlit の起動を切り替える。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from streamlit.web import bootstrap

from crm_poc import exe_probe, lock_probe, paths, scrypt_check

SERVE_COMMAND = "serve"
PROBE_COMMAND = "probe"
SCRYPT_COMMAND = "scrypt"
LOCK_COMMAND = "lock"
DEFAULT_PORT = 8765
MINIMUM_PORT = 1
MAXIMUM_PORT = 65535
HOST = "127.0.0.1"
USAGE_EXIT_CODE = 2
MINIMUM_ARGUMENT_COUNT = 1
MAXIMUM_SERVE_ARGUMENT_COUNT = 2
PORT_ARGUMENT_INDEX = 1

HEADLESS_OPTION = "server_headless"
ADDRESS_OPTION = "server_address"
PORT_OPTION = "server_port"
FILE_WATCHER_OPTION = "server_fileWatcherType"
USAGE_STATS_OPTION = "browser_gatherUsageStats"
DEVELOPMENT_MODE_OPTION = "global_developmentMode"
FILE_WATCHER_NONE = "none"


def _parse_port(arguments: list[str]) -> int | None:
    if len(arguments) > MAXIMUM_SERVE_ARGUMENT_COUNT:
        return None
    if len(arguments) == MAXIMUM_SERVE_ARGUMENT_COUNT:
        try:
            port = int(arguments[PORT_ARGUMENT_INDEX])
        except ValueError:
            return None
        if MINIMUM_PORT <= port <= MAXIMUM_PORT:
            return port
        return None
    return DEFAULT_PORT


def _serve(arguments: list[str]) -> int:
    port = _parse_port(arguments)
    if port is None:
        return USAGE_EXIT_CODE
    script = paths.exe_ui_directory() / paths.EXE_APP_FILE_NAME
    flag_options: dict[str, str | int | bool] = {
        HEADLESS_OPTION: True,
        ADDRESS_OPTION: HOST,
        PORT_OPTION: port,
        FILE_WATCHER_OPTION: FILE_WATCHER_NONE,
        USAGE_STATS_OPTION: False,
        DEVELOPMENT_MODE_OPTION: False,
    }
    bootstrap.load_config_options(flag_options=flag_options)
    bootstrap.run(str(script), False, [], flag_options)
    return 0


def main(arguments: list[str]) -> int:
    if len(arguments) < MINIMUM_ARGUMENT_COUNT:
        return USAGE_EXIT_CODE
    command = arguments[0]
    if command == SERVE_COMMAND:
        return _serve(arguments)
    if command == PROBE_COMMAND:
        print(json.dumps(asdict(exe_probe.run_all_checks()), ensure_ascii=False))
        return 0
    if command == SCRYPT_COMMAND:
        return scrypt_check.main()
    if command == LOCK_COMMAND:
        return lock_probe.main(arguments[1:])
    return USAGE_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
