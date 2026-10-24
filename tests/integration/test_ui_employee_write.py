"""社員の登録・編集画面の試験（`SCN-022`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "employee.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30
_TIMESTAMP = "2026-10-24T00:00:00.000000Z"


@pytest.fixture
def localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "crm-g.db"
    with open_connection(db_path) as connection:
        apply_migrations(connection, _DDL_DIR, db_path, data_dir / "backups")
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) "
            "VALUES (1, 'taro@example.com', '在籍太郎', '在籍', '2020-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
    return tmp_path


def _new_app() -> AppTest:
    return AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def test_register_new_employee(localappdata: Path) -> None:
    app = _new_app()

    app.button(key="employee_open_register").click()
    app = app.run()

    assert "社員の登録" in [t.value for t in app.title]

    suffix = "new"
    app.text_input(key=f"employee_email_{suffix}").set_value("jiro@example.com")
    app.text_input(key=f"employee_name_{suffix}").set_value("次郎")
    app.date_input(key=f"employee_join_date_{suffix}").set_value(date(2026, 4, 1))
    app.selectbox(key=f"employee_enrollment_status_{suffix}").set_value("在籍")
    app.button(key=f"employee_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    assert "社員詳細" in [t.value for t in app.title]

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT name FROM employee WHERE email = 'jiro@example.com'"
        ).fetchone()
    assert row == ("次郎",)


def test_register_requires_name(localappdata: Path) -> None:
    app = _new_app()

    app.button(key="employee_open_register").click()
    app = app.run()

    suffix = "new"
    app.text_input(key=f"employee_email_{suffix}").set_value("jiro@example.com")
    app.date_input(key=f"employee_join_date_{suffix}").set_value(date(2026, 4, 1))
    app.selectbox(key=f"employee_enrollment_status_{suffix}").set_value("在籍")
    app.button(key=f"employee_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    assert any("氏名" in error.value for error in app.error)

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM employee WHERE email = 'jiro@example.com'"
        ).fetchone()
    assert row is None


def test_register_requires_join_date(localappdata: Path) -> None:
    app = _new_app()

    app.button(key="employee_open_register").click()
    app = app.run()

    suffix = "new"
    app.text_input(key=f"employee_email_{suffix}").set_value("jiro@example.com")
    app.text_input(key=f"employee_name_{suffix}").set_value("次郎")
    app.selectbox(key=f"employee_enrollment_status_{suffix}").set_value("在籍")
    # 入社日は入力しない（st.date_input の初期値 None のまま）。
    app.button(key=f"employee_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    assert any("入社日" in error.value for error in app.error)

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM employee WHERE email = 'jiro@example.com'"
        ).fetchone()
    assert row is None


def test_edit_existing_employee_prefills_and_saves(localappdata: Path) -> None:
    app = _new_app()

    app.selectbox(key="employee_list_selected").set_value("1：在籍太郎")
    app.button(key="employee_open_detail").click()
    app = app.run()

    app.button(key="employee_open_edit").click()
    app = app.run()

    suffix = "1"
    assert app.text_input(key=f"employee_email_{suffix}").value == "taro@example.com"
    assert app.text_input(key=f"employee_name_{suffix}").value == "在籍太郎"

    app.text_input(key=f"employee_name_{suffix}").set_value("在籍太郎（改）")
    app.button(key=f"employee_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    assert "社員詳細" in [t.value for t in app.title]

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute("SELECT name, email FROM employee WHERE id = 1").fetchone()
    assert row == ("在籍太郎（改）", "taro@example.com")


def test_cancel_edit_returns_to_detail_without_saving(localappdata: Path) -> None:
    app = _new_app()

    app.selectbox(key="employee_list_selected").set_value("1：在籍太郎")
    app.button(key="employee_open_detail").click()
    app = app.run()

    app.button(key="employee_open_edit").click()
    app = app.run()

    suffix = "1"
    app.text_input(key=f"employee_name_{suffix}").set_value("変更したが保存しない")
    app.button(key=f"employee_cancel_{suffix}").click()
    app = app.run()

    assert not app.exception
    assert "社員詳細" in [t.value for t in app.title]

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute("SELECT name FROM employee WHERE id = 1").fetchone()
    assert row == ("在籍太郎",)


def test_duplicate_email_shows_error(localappdata: Path) -> None:
    app = _new_app()

    app.button(key="employee_open_register").click()
    app = app.run()

    suffix = "new"
    app.text_input(key=f"employee_email_{suffix}").set_value("TARO@EXAMPLE.COM")
    app.text_input(key=f"employee_name_{suffix}").set_value("別の太郎")
    app.date_input(key=f"employee_join_date_{suffix}").set_value(date(2026, 4, 1))
    app.selectbox(key=f"employee_enrollment_status_{suffix}").set_value("在籍")
    app.button(key=f"employee_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    assert any("既に使われています" in error.value for error in app.error)
