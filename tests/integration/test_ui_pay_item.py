"""給与項目画面の試験（`SCN-062`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "pay_item.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30
_NEW_ITEM_LABEL = "（新規追加）"


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


def test_shows_seed_pay_items(localappdata: Path) -> None:
    app = _new_app()

    assert len(app.dataframe) == 1
    assert not app.exception


def test_create_new_pay_item_through_confirm_dialog(localappdata: Path) -> None:
    app = _new_app()

    app.selectbox(key="pay_item_selected_code").set_value(_NEW_ITEM_LABEL)
    app = app.run()

    suffix = _NEW_ITEM_LABEL
    app.text_input(key=f"pay_item_code_{suffix}").set_value("NEW_ITEM")
    app.text_input(key=f"pay_item_name_{suffix}").set_value("新手当")
    app.selectbox(key=f"pay_item_category_{suffix}").set_value("固定支給")
    app.selectbox(key=f"pay_item_in_monthly_salary_{suffix}").set_value("○")
    app.selectbox(key=f"pay_item_cost_direct_{suffix}").set_value("×")
    app.date_input(key=f"pay_item_valid_from_{suffix}").set_value(date(2026, 4, 1))
    app.number_input(key=f"pay_item_sort_order_{suffix}").set_value(99)
    app.button(key=f"pay_item_submit_{suffix}").click()
    app = app.run()

    app.button(key="pay_item_confirm_confirm").click()
    app = app.run()

    assert not app.exception

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT name, in_monthly_salary, cost_direct FROM pay_item WHERE code = 'NEW_ITEM'"
        ).fetchone()
    assert row == ("新手当", 1, 0)


def test_mutually_exclusive_flags_shows_error_after_dialog_closes(localappdata: Path) -> None:
    app = _new_app()

    app.selectbox(key="pay_item_selected_code").set_value(_NEW_ITEM_LABEL)
    app = app.run()

    suffix = _NEW_ITEM_LABEL
    app.text_input(key=f"pay_item_code_{suffix}").set_value("NEW_ITEM")
    app.text_input(key=f"pay_item_name_{suffix}").set_value("新手当")
    app.selectbox(key=f"pay_item_in_monthly_salary_{suffix}").set_value("○")
    app.selectbox(key=f"pay_item_cost_direct_{suffix}").set_value("○")
    app.date_input(key=f"pay_item_valid_from_{suffix}").set_value(date(2026, 4, 1))
    app.button(key=f"pay_item_submit_{suffix}").click()
    app = app.run()

    app.button(key="pay_item_confirm_confirm").click()
    app = app.run()

    assert not app.exception
    assert any("同時に○" in error.value for error in app.error)

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute("SELECT 1 FROM pay_item WHERE code = 'NEW_ITEM'").fetchone()
    assert row is None


def test_select_existing_item_prefills_the_form(localappdata: Path) -> None:
    """選択を切り替えると、フォームが選択した項目の値で再表示される（既知の不具合の回帰試験）。"""
    app = _new_app()

    app.selectbox(key="pay_item_selected_code").set_value("BASE_SALARY")
    app = app.run()

    suffix = "BASE_SALARY"
    assert app.text_input(key=f"pay_item_code_{suffix}").value == "BASE_SALARY"
    assert app.text_input(key=f"pay_item_name_{suffix}").value == "基本給"
    assert app.selectbox(key=f"pay_item_category_{suffix}").value == "基本給"


def test_edit_existing_item_through_confirm_dialog(localappdata: Path) -> None:
    app = _new_app()

    app.selectbox(key="pay_item_selected_code").set_value("HOUSING")
    app = app.run()

    suffix = "HOUSING"
    app.text_input(key=f"pay_item_name_{suffix}").set_value("住宅手当（改）")
    app.button(key=f"pay_item_submit_{suffix}").click()
    app = app.run()

    app.button(key="pay_item_confirm_confirm").click()
    app = app.run()

    assert not app.exception

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute("SELECT name FROM pay_item WHERE code = 'HOUSING'").fetchone()
    assert row == ("住宅手当（改）",)
