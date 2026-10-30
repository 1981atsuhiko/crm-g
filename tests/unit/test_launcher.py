"""起動用launcherの試験（`ARC-014`）。"""

import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import crm_g.cli.launcher as launcher_module
from crm_g.infrastructure.single_instance import AlreadyRunningError


def test_build_flag_options_matches_approved_settings() -> None:
    options = launcher_module._build_flag_options()

    assert options["server_headless"] is True
    assert options["server_address"] == "127.0.0.1"
    assert options["server_port"] == 8501
    assert options["server_enableXsrfProtection"] is True
    assert options["server_enableCORS"] is True
    assert options["server_allowedHosts"] == ["localhost", "127.0.0.1"]
    assert options["browser_gatherUsageStats"] is False
    assert options["client_showErrorDetails"] == "none"
    assert options["global_developmentMode"] is False


def test_is_port_available_returns_true_for_free_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]

    assert launcher_module._is_port_available("127.0.0.1", free_port) is True


def test_is_port_available_returns_false_for_port_in_use() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]

        assert launcher_module._is_port_available("127.0.0.1", port) is False


def test_serve_returns_error_when_already_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    @contextmanager
    def _raising_lock(lock_path: Path) -> Iterator[None]:
        raise AlreadyRunningError("既に起動しています")
        yield  # pragma: no cover

    monkeypatch.setattr(launcher_module, "acquire_lock", _raising_lock)

    exit_code = launcher_module.serve()

    assert exit_code == 1
    assert "既に起動しています" in capsys.readouterr().out


def test_serve_returns_error_when_port_in_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(launcher_module, "_is_port_available", lambda host, port: False)

    exit_code = launcher_module.serve()

    assert exit_code == 1
    assert "使用中です" in capsys.readouterr().out


def test_serve_returns_error_on_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """migrations以降で例外が起きても、Streamlitの起動（ブロッキング）には進まない。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(launcher_module, "_is_port_available", lambda host, port: True)

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("擬似的な失敗")

    monkeypatch.setattr(launcher_module, "apply_migrations", _raise)

    exit_code = launcher_module.serve()

    assert exit_code == 1
    assert "起動に失敗しました" in capsys.readouterr().out


def test_main_without_arguments_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    """引数がなければ、今までどおり画面を起動する。"""
    monkeypatch.setattr(launcher_module, "serve", lambda: 7)

    assert launcher_module.main([]) == 7


def test_main_dispatches_import_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`import` に続く引数を、移行コマンドに渡す（移行設計書 `MIG-070`）。"""
    received: list[list[str]] = []

    def _fake(arguments: list[str]) -> int:
        received.append(list(arguments))
        return 1

    monkeypatch.setattr(launcher_module, "run_import_command", _fake)

    assert launcher_module.main(["import", "employee", "a.csv", "--check"]) == 1
    assert received == [["employee", "a.csv", "--check"]]


def test_main_reads_sys_argv_when_no_arguments_are_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["crm-g"])
    monkeypatch.setattr(launcher_module, "serve", lambda: 0)

    assert launcher_module.main() == 0


def test_main_rejects_unknown_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert launcher_module.main(["unknown"]) == 2
    assert "使い方:" in capsys.readouterr().err


def test_main_help_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert launcher_module.main(["--help"]) == 0
    assert "crm-g import" in capsys.readouterr().out
