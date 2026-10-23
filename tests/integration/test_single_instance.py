"""多重起動の防止の試験（TC-ARC-008）。"""

import msvcrt
import sys
from pathlib import Path

import pytest

from crm_g.infrastructure.paths import UnsupportedPlatformError
from crm_g.infrastructure.single_instance import AlreadyRunningError, acquire_lock


def test_tc_arc_008_second_startup_is_rejected_while_locked(tmp_path: Path) -> None:
    """データ領域のロックを取得した状態で、もう一度起動する（TC-ARC-008前段）。"""
    lock_path = tmp_path / "app.lock"

    with (
        acquire_lock(lock_path),
        pytest.raises(AlreadyRunningError),
        acquire_lock(lock_path),
    ):
        pass


def test_tc_arc_008_startup_succeeds_after_lock_is_released(tmp_path: Path) -> None:
    """ロックを解放して、もう一度起動する（TC-ARC-008後段）。"""
    lock_path = tmp_path / "app.lock"

    with acquire_lock(lock_path):
        pass

    with acquire_lock(lock_path):
        pass


def test_tc_arc_008_lock_can_be_reacquired_after_abrupt_close(tmp_path: Path) -> None:
    """異常終了で残ったロックは、強制終了の後に再び取得できる（TC-ARC-008。POC-06）。

    OSの排他ロックは、ロックを解放する呼び出しをせずにファイルを閉じても、
    OSが自動的に解放する（Windowsの `msvcrt.locking` の性質）。
    """
    lock_path = tmp_path / "app.lock"
    lock_path.write_bytes(b"0")

    abandoned_file = lock_path.open("r+b")
    msvcrt.locking(abandoned_file.fileno(), msvcrt.LK_NBLCK, 1)
    abandoned_file.close()

    with acquire_lock(lock_path):
        pass


def test_acquire_lock_rejects_unsupported_os(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    with (
        pytest.raises(UnsupportedPlatformError, match="対応していないOS"),
        acquire_lock(tmp_path / "app.lock"),
    ):
        pass
