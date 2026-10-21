from __future__ import annotations

import json
from datetime import date

import pytest
from pytest_mock import MockerFixture
from streamlit.testing.v1 import AppTest

from crm_poc import exe_probe, launcher, paths, settings

EXE_APP_PATH = paths.exe_ui_directory() / paths.EXE_APP_FILE_NAME
APPTEST_TIMEOUT_SECONDS = 30


def test_read_bundled_sql_item() -> None:
    assert exe_probe.read_bundled_sql_item() == "bundled-sql-ok"


@pytest.mark.parametrize(
    ("target", "expected_name"),
    [
        (date(2026, 1, 1), "元日"),
        (date(2026, 1, 2), None),
    ],
)
def test_holiday_name(target: date, expected_name: str | None) -> None:
    assert exe_probe.holiday_name(target) == expected_name


@pytest.mark.parametrize(
    ("distribution_name", "is_listed"),
    [
        ("streamlit", True),
        ("jpholiday", True),
        ("not-installed-package", False),
    ],
)
def test_find_license(distribution_name: str, is_listed: bool) -> None:
    license_name = exe_probe.find_license(exe_probe.list_licenses(), distribution_name)

    assert (license_name is not None) is is_listed


def test_find_license_returns_none_for_empty_entries() -> None:
    assert exe_probe.find_license([], "streamlit") is None


def test_paths_are_not_frozen_in_tests() -> None:
    assert paths.is_frozen() is False
    assert paths.schema_path().is_file()


def test_launcher_probe_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = launcher.main([launcher.PROBE_COMMAND])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["sql_item"] == "bundled-sql-ok"
    assert result["holiday_new_year"] == "元日"
    assert result["holiday_next_day"] is None


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["unknown"],
        ["serve", "abc"],
        ["serve", "0"],
        ["serve", "65536"],
        ["serve", "8765", "extra"],
    ],
)
def test_launcher_usage_error(arguments: list[str]) -> None:
    assert launcher.main(arguments) == launcher.USAGE_EXIT_CODE


@pytest.mark.parametrize(
    ("arguments", "expected_port"),
    [
        (["serve"], 8765),
        (["serve", "1"], 1),
        (["serve", "65535"], 65535),
    ],
)
def test_launcher_serve_passes_options_to_streamlit(
    mocker: MockerFixture, arguments: list[str], expected_port: int
) -> None:
    bootstrap = mocker.patch("crm_poc.launcher.bootstrap")

    exit_code = launcher.main(arguments)

    expected_options = {
        "server_headless": True,
        "server_address": "127.0.0.1",
        "server_port": expected_port,
        "server_fileWatcherType": "none",
        "browser_gatherUsageStats": False,
        "global_developmentMode": False,
    }
    assert exit_code == 0
    bootstrap.load_config_options.assert_called_once_with(flag_options=expected_options)
    bootstrap.run.assert_called_once_with(str(EXE_APP_PATH), False, [], expected_options)


def test_exe_app_shows_probe_as_default_page() -> None:
    app = AppTest.from_file(str(EXE_APP_PATH), default_timeout=APPTEST_TIMEOUT_SECONDS).run()

    assert not app.exception
    assert [title.value for title in app.title] == [settings.EXE_PROBE_PATH]


def test_exe_app_licenses_page_lists_streamlit() -> None:
    app = AppTest.from_file(str(EXE_APP_PATH), default_timeout=APPTEST_TIMEOUT_SECONDS).run()
    app.switch_page(f"{settings.EXE_VIEWS_DIRECTORY_NAME}/{settings.EXE_LICENSES_VIEW_FILE}")
    app.run()

    assert not app.exception
    assert [title.value for title in app.title] == [settings.EXE_LICENSES_PATH]
    assert len(app.get("dataframe")) == 1
