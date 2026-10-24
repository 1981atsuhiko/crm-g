"""計算設定画面の試験（`SCN-063`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "crm_g"
    / "ui"
    / "views"
    / "calculation_setting.py"
)
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30


@pytest.fixture
def localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "crm-g.db"
    with open_connection(db_path) as connection:
        apply_migrations(connection, _DDL_DIR, db_path, data_dir / "backups")
    return tmp_path


def _new_app() -> AppTest:
    return AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def test_shows_rounding_and_history(localappdata: Path) -> None:
    app = _new_app()

    assert "現在の値：切り上げ（変更できません）" in [m.value for m in app.markdown]
    assert len(app.dataframe) == 1
    assert not app.exception


def test_change_coefficient_through_confirm_dialog(localappdata: Path) -> None:
    app = _new_app()

    app.text_input(key="calculation_setting_coefficient").set_value("1.5000")
    app.date_input(key="calculation_setting_valid_from").set_value(date(2026, 4, 1))
    app.button(key="calculation_setting_submit").click()
    app = app.run()

    app.button(key="calculation_setting_confirm_confirm").click()
    app = app.run()

    assert not app.exception

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT value_integer FROM system_setting "
            "WHERE setting_key = '原価係数' AND valid_to IS NULL"
        ).fetchone()
    assert row == (15000,)


def test_invalid_coefficient_shows_error_after_dialog_closes(localappdata: Path) -> None:
    app = _new_app()

    app.text_input(key="calculation_setting_coefficient").set_value("0")
    app.date_input(key="calculation_setting_valid_from").set_value(date(2026, 4, 1))
    app.button(key="calculation_setting_submit").click()
    app = app.run()

    app.button(key="calculation_setting_confirm_confirm").click()
    app = app.run()

    assert not app.exception
    assert any("0より大きい" in error.value for error in app.error)
