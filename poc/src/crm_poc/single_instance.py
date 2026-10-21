"""POC-06：OS の排他ロックによる多重起動の防止。

Windows は msvcrt.locking、それ以外（macOS）は fcntl.flock を使う（実装計画書 IPD-007）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class AlreadyRunningError(Exception):
    """他のプロセスが、すでにロックを取得している。"""


def _try_lock(file_descriptor: int) -> None:
    if sys.platform == "win32":
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file_descriptor: int | None = None

    def acquire(self) -> None:
        file_descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT)
        try:
            _try_lock(file_descriptor)
        except OSError as error:
            os.close(file_descriptor)
            raise AlreadyRunningError(str(self._path)) from error
        self._file_descriptor = file_descriptor

    def release(self) -> None:
        if self._file_descriptor is None:
            return
        os.close(self._file_descriptor)
        self._file_descriptor = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
