"""稼働状態画面の試験（`SCN-061`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "work_status.py"
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


def test_shows_seed_work_statuses(localappdata: Path) -> None:
    app = _new_app()

    assert len(app.dataframe) == 2  # 一覧 + 支給率の履歴
    assert not app.exception


def test_change_rate_through_confirm_dialog(localappdata: Path) -> None:
    app = _new_app()

    app.selectbox(key="work_status_selected_code").set_value("DEV")
    app = app.run()

    app.text_input(key="work_status_rate_percent").set_value("85.50")
    app.date_input(key="work_status_rate_valid_from").set_value(date(2026, 4, 1))
    app.button(key="work_status_rate_submit").click()
    app = app.run()

    app.button(key="work_status_rate_confirm_confirm").click()
    app = app.run()

    assert not app.exception

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT payroll_rate_bp, valid_to FROM work_status_rate "
            "WHERE work_status_code = 'DEV' AND valid_to IS NULL"
        ).fetchone()
    assert row == (8550, None)


def test_invalid_rate_shows_error_inside_dialog_flow(localappdata: Path) -> None:
    app = _new_app()

    app.selectbox(key="work_status_selected_code").set_value("DEV")
    app = app.run()

    app.text_input(key="work_status_rate_percent").set_value("100.01")
    app.date_input(key="work_status_rate_valid_from").set_value(date(2026, 4, 1))
    app.button(key="work_status_rate_submit").click()
    app = app.run()

    app.button(key="work_status_rate_confirm_confirm").click()
    app = app.run()

    assert not app.exception
    assert any("0〜100" in error.value for error in app.error)

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM work_status_rate WHERE work_status_code = 'DEV'"
        ).fetchone()[0]
    assert count == 1
