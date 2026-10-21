"""POC-06 の確認用：ロックを取得して待つ（hold）、または取得を試す（try）。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from crm_poc import settings
from crm_poc.single_instance import AlreadyRunningError, InstanceLock

HOLD_COMMAND = "hold"
TRY_COMMAND = "try"
HOLD_POLL_SECONDS = 0.1
USAGE_EXIT_CODE = 2


def _hold(path: Path) -> int:
    with InstanceLock(path):
        print(settings.LOCK_READY_MESSAGE, flush=True)
        while True:
            time.sleep(HOLD_POLL_SECONDS)


def _try(path: Path) -> int:
    try:
        with InstanceLock(path):
            return 0
    except AlreadyRunningError:
        return settings.LOCK_FAILED_EXIT_CODE


def main(arguments: list[str]) -> int:
    expected_argument_count = 2
    if len(arguments) != expected_argument_count:
        return USAGE_EXIT_CODE
    command, lock_path = arguments
    if command == HOLD_COMMAND:
        return _hold(Path(lock_path))
    if command == TRY_COMMAND:
        return _try(Path(lock_path))
    return USAGE_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
