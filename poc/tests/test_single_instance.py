from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from crm_poc import settings
from crm_poc.single_instance import AlreadyRunningError, InstanceLock

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
PROCESS_TIMEOUT_SECONDS = 30


def _probe_command(command: str, lock_path: Path) -> list[str]:
    return [sys.executable, "-m", "crm_poc.lock_probe", command, str(lock_path)]


def _probe_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(SOURCE_DIRECTORY)
    return environment


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / settings.LOCK_FILE_NAME


@pytest.fixture
def holder(lock_path: Path) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        _probe_command("hold", lock_path),
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=_probe_environment(),
    )
    watchdog = threading.Timer(PROCESS_TIMEOUT_SECONDS, process.kill)
    watchdog.start()
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == settings.LOCK_READY_MESSAGE
        yield process
    finally:
        watchdog.cancel()
        process.kill()
        process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
        if process.stdout is not None:
            process.stdout.close()


def _run_try(lock_path: Path) -> int:
    completed = subprocess.run(
        _probe_command("try", lock_path),
        check=False,
        timeout=PROCESS_TIMEOUT_SECONDS,
        env=_probe_environment(),
    )
    return completed.returncode


def test_second_process_fails_to_acquire(holder: subprocess.Popen[str], lock_path: Path) -> None:
    assert holder.poll() is None
    assert _run_try(lock_path) == settings.LOCK_FAILED_EXIT_CODE


def test_lock_can_be_acquired_after_holder_is_killed(
    holder: subprocess.Popen[str], lock_path: Path
) -> None:
    holder.kill()
    holder.wait(timeout=PROCESS_TIMEOUT_SECONDS)

    assert _run_try(lock_path) == 0


def test_acquire_twice_in_same_process_via_separate_descriptors_fails(
    lock_path: Path,
) -> None:
    with InstanceLock(lock_path), pytest.raises(AlreadyRunningError):
        InstanceLock(lock_path).acquire()


def test_lock_can_be_reacquired_after_release(lock_path: Path) -> None:
    lock = InstanceLock(lock_path)
    lock.acquire()
    lock.release()

    with InstanceLock(lock_path):
        pass


def test_release_without_acquire_is_noop(lock_path: Path) -> None:
    InstanceLock(lock_path).release()
