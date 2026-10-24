"""社員画面の試験（`SCN-020`、`SCN-021`）。"""

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
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) "
            "VALUES (2, 'jiro@example.com', '退職次郎', '退職', '2019-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
    return tmp_path


def _new_app() -> AppTest:
    return AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def test_tc_fun_005_default_filter_shows_active_employee_only(localappdata: Path) -> None:
    app = _new_app()

    assert len(app.dataframe) == 1
    assert not app.exception


def test_open_detail_and_back_to_list(localappdata: Path) -> None:
    app = _new_app()

    app.selectbox(key="employee_list_selected").set_value("1：在籍太郎")
    app.button(key="employee_open_detail").click()
    app = app.run()

    assert "社員詳細" in [t.value for t in app.title]
    assert not app.exception

    app.button(key="employee_back_to_list").click()
    app = app.run()

    assert "社員一覧" in [t.value for t in app.title]


def test_detail_records_view_log_once(localappdata: Path) -> None:
    app = _new_app()

    app.selectbox(key="employee_list_selected").set_value("1：在籍太郎")
    app.button(key="employee_open_detail").click()
    app = app.run()
    app = app.run()  # 同じ対象への再実行では、記録が増えないことを確認する。

    assert not app.exception

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM operation_log WHERE operation = '閲覧'"
        ).fetchone()[0]
    # salary_history・salary_history_item・bonusの3テーブル分、1回だけ記録される。
    assert count == 3


def test_search_narrows_the_list(localappdata: Path) -> None:
    app = _new_app()

    app.multiselect(key="employee_filter_statuses").set_value(["在籍", "退職予定", "退職"])
    app = app.run()
    assert len(app.dataframe[0].value) == 2

    app.text_input(key="employee_search").set_value("次郎")
    app = app.run()

    assert len(app.dataframe[0].value) == 1
