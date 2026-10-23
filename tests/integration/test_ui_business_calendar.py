"""営業日カレンダー画面の試験（`SCN-060`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "business_calendar.py"
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


def test_shows_create_button_when_year_not_created(localappdata: Path) -> None:
    app = _new_app()

    assert [button.key for button in app.button] == ["create_calendar"]
    assert not app.exception


def test_create_calendar_then_shows_month_table_and_refetch_button(localappdata: Path) -> None:
    app = _new_app()
    app.button(key="create_calendar").click()
    app = app.run()

    assert "refetch_holidays" in [button.key for button in app.button]
    assert len(app.dataframe) == 1
    assert not app.exception


def test_register_manual_day_through_confirm_dialog(localappdata: Path) -> None:
    app = _new_app()
    app.button(key="create_calendar").click()
    app = app.run()

    app.date_input(key="register_day_date").set_value(date(2026, 1, 5))
    app.radio(key="register_day_choice").set_value("非営業日")
    app.text_input(key="register_day_note").set_value("会社休日")
    app.button(key="register_day_submit").click()
    app = app.run()

    # 確認ダイアログの「実行」を押す。
    app.button(key="register_day_confirm_confirm").click()
    app = app.run()

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT is_business_day, source, note FROM business_calendar "
            "WHERE calendar_date = '2026-01-05'"
        ).fetchone()
    assert row == (0, "手動登録", "会社休日")
