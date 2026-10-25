"""稼働画面（参画・単価タブ）の試験（`SCN-050`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.assignment import AssignmentInput, create_assignment
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "work.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30
_TIMESTAMP = "2026-10-25T00:00:00.000000Z"


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "crm-g.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, data_dir / "backups")
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) "
            "VALUES (1, 'taro@example.com', '山田太郎', '在籍', '2020-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
    return path


def _new_app() -> AppTest:
    return AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def _count(db_path: Path, table: str) -> int:
    with open_connection(db_path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 試験用の固定のテーブル名


def test_shows_three_tabs(db_path: Path) -> None:
    app = _new_app()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["稼働期間", "参画", "単価"]


def test_register_assignment(db_path: Path) -> None:
    app = _new_app()

    app.text_input(key="work_assignment_customer_1_new").set_value("A社")
    app.date_input(key="work_assignment_start_1_new").set_value(date(2026, 4, 1))
    app.button(key="work_assignment_submit_1_new").click()
    app = app.run()

    assert not app.exception
    assert any("参画を登録しました" in toast.value for toast in app.toast)
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT employee_id, customer_name, category, start_date, end_date FROM assignment"
        ).fetchone()
    assert row == (1, "A社", "SES", "2026-04-01", None)
    # 登録後は、新規登録の入力欄が空に戻る（二重登録を防ぐ）。
    assert app.text_input(key="work_assignment_customer_1_new").value == ""
    assert app.date_input(key="work_assignment_start_1_new").value is None


def test_rate_list_shows_assignment_label_or_employee_level(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(
            connection,
            1,
            AssignmentInput(
                customer_name="A社",
                project_name="基幹",
                category="SES",
                start_date=date(2026, 4, 1),
                end_date=None,
            ),
        )
    app = _new_app()
    for assignment_value, effective_from in (
        (f"{assignment_id}：A社／基幹（2026-04-01〜）", date(2026, 4, 1)),
        ("（社員単位の単価）", date(2026, 10, 1)),
    ):
        app.selectbox(key="work_rate_assignment_1").set_value(assignment_value)
        app.date_input(key="work_rate_effective_from_1").set_value(effective_from)
        app.text_input(key="work_rate_unit_price_1").set_value("700000")
        app.button(key="work_rate_submit_1").click()
        app = app.run()

    assert not app.exception
    rows = app.dataframe[-1].value.to_dict("records")
    assert [row["参画"] for row in rows] == ["A社／基幹", "（社員単位）"]


def test_register_assignment_without_start_date_shows_error(db_path: Path) -> None:
    app = _new_app()

    app.text_input(key="work_assignment_customer_1_new").set_value("A社")
    app.button(key="work_assignment_submit_1_new").click()
    app = app.run()

    assert any("開始日を入力してください" in error.value for error in app.error)
    assert _count(db_path, "assignment") == 0


def test_select_existing_assignment_prefills_and_updates(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(
            connection,
            1,
            AssignmentInput(
                customer_name="A社",
                project_name=None,
                category="受託",
                start_date=date(2026, 4, 1),
                end_date=None,
            ),
        )
    app = _new_app()

    app.selectbox(key="work_assignment_selected_1").set_value(assignment_id)
    app = app.run()
    suffix = f"1_{assignment_id}"
    assert app.text_input(key=f"work_assignment_customer_{suffix}").value == "A社"
    assert app.selectbox(key=f"work_assignment_category_{suffix}").value == "受託"

    app.date_input(key=f"work_assignment_end_{suffix}").set_value(date(2026, 9, 30))
    app.button(key=f"work_assignment_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    with open_connection(db_path) as connection:
        end_date = connection.execute("SELECT end_date FROM assignment").fetchone()[0]
    assert end_date == "2026-09-30"
    # 表示名が変わっても、更新した参画が選ばれたままになる。
    assert app.selectbox(key="work_assignment_selected_1").value == assignment_id
    assert app.date_input(key=f"work_assignment_end_{suffix}").value == date(2026, 9, 30)


def test_register_rate(db_path: Path) -> None:
    app = _new_app()

    app.date_input(key="work_rate_effective_from_1").set_value(date(2026, 4, 1))
    app.text_input(key="work_rate_unit_price_1").set_value("700000")
    app.button(key="work_rate_submit_1").click()
    app = app.run()

    assert not app.exception
    assert any("単価を登録しました" in toast.value for toast in app.toast)
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT employee_id, assignment_id, effective_from, unit_price FROM rate_history"
        ).fetchone()
    assert row == (1, None, "2026-04-01", 700000)


def test_register_rate_with_non_integer_price_shows_error(db_path: Path) -> None:
    app = _new_app()

    app.date_input(key="work_rate_effective_from_1").set_value(date(2026, 4, 1))
    app.text_input(key="work_rate_unit_price_1").set_value("abc")
    app.button(key="work_rate_submit_1").click()
    app = app.run()

    assert any("0以上の整数" in error.value for error in app.error)
    assert _count(db_path, "rate_history") == 0


def test_register_duplicate_rate_shows_error(db_path: Path) -> None:
    app = _new_app()
    for _ in range(2):
        app.date_input(key="work_rate_effective_from_1").set_value(date(2026, 4, 1))
        app.text_input(key="work_rate_unit_price_1").set_value("700000")
        app.button(key="work_rate_submit_1").click()
        app = app.run()

    assert any("既に登録されています" in error.value for error in app.error)
    assert _count(db_path, "rate_history") == 1
