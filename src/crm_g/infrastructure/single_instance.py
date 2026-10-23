"""多重起動の防止（ARC-007）。

起動時に、データ領域のロックファイルを排他的に取得する。取得できなければ、
起動中の旨を表示して終了する。ロックは、OSの排他ロック（Windows は
`msvcrt.locking`）とする（実装計画書 IPD-007）。ロックファイルの存在だけで
判定すると、異常終了後に残るため。プロセスの終了（異常終了を含む）で、
OSが自動的にロックを解放することを前提とする（実装計画書 POC-06 で確認済み）。
"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from crm_g.infrastructure.paths import UnsupportedPlatformError


class AlreadyRunningError(RuntimeError):
    """既に起動しているため、ロックを取得できなかったときのエラー。"""


@contextmanager
def acquire_lock(lock_path: Path) -> Iterator[None]:
    """データ領域のロックファイルを、排他的に取得する。"""
    if sys.platform != "win32":
        raise UnsupportedPlatformError(f"対応していないOSです: {sys.platform}")

    import msvcrt  # noqa: PLC0415 Windows専用のため、この関数内だけでimportする

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not lock_path.exists():
        lock_path.write_bytes(b"0")

    file = lock_path.open("r+b")
    try:
        try:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise AlreadyRunningError("既に起動しています") from error

        try:
            yield
        finally:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        file.close()
