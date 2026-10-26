"""稼働画面（参画のタブと、参画の単価）の試験（`SCN-050`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.assignment import AssignmentInput, create_assignment
from crm_g.application.unit_price import RateHistoryInput, register_rate_history
from crm_g.domain.period import current_fiscal_half
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


def _create_assignment(db_path: Path, end_date: date | None = None) -> int:
    with open_connection(db_path) as connection:
        return create_assignment(
            connection,
            1,
            AssignmentInput(
                customer_name="A社",
                project_name=None,
                category="受託",
                start_date=date(2026, 4, 1),
                end_date=end_date,
            ),
        )


def _open_assignment(app: AppTest, assignment_id: int) -> AppTest:
    app.selectbox(key="work_assignment_selected_1").set_value(assignment_id)
    return app.run()


def test_shows_two_tabs(db_path: Path) -> None:
    app = _new_app()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["稼働期間", "参画"]


def test_register_assignment_clears_inputs(db_path: Path) -> None:
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


def test_register_assignment_without_start_date_shows_error(db_path: Path) -> None:
    app = _new_app()

    app.text_input(key="work_assignment_customer_1_new").set_value("A社")
    app.button(key="work_assignment_submit_1_new").click()
    app = app.run()

    assert any("開始日を入力してください" in error.value for error in app.error)
    assert _count(db_path, "assignment") == 0


def test_rate_section_is_shown_only_for_selected_assignment(db_path: Path) -> None:
    assignment_id = _create_assignment(db_path)
    app = _new_app()

    assert not [box for box in app.selectbox if box.key == f"work_rate_selected_{assignment_id}"]

    app = _open_assignment(app, assignment_id)

    assert app.selectbox(key=f"work_rate_selected_{assignment_id}").value is None


def test_select_existing_assignment_prefills_and_updates(db_path: Path) -> None:
    assignment_id = _create_assignment(db_path)
    app = _open_assignment(_new_app(), assignment_id)
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


def test_register_rate_for_assignment_clears_inputs(db_path: Path) -> None:
    assignment_id = _create_assignment(db_path)
    app = _open_assignment(_new_app(), assignment_id)
    suffix = f"{assignment_id}_new"

    app.date_input(key=f"work_rate_effective_from_{suffix}").set_value(date(2026, 4, 1))
    app.text_input(key=f"work_rate_unit_price_{suffix}").set_value("700000")
    app.button(key=f"work_rate_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    assert any("単価を登録しました" in toast.value for toast in app.toast)
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT employee_id, assignment_id, effective_from, unit_price FROM rate_history"
        ).fetchone()
    assert row == (1, assignment_id, "2026-04-01", 700000)
    assert app.text_input(key=f"work_rate_unit_price_{suffix}").value == ""


def test_register_rate_outside_assignment_shows_error(db_path: Path) -> None:
    assignment_id = _create_assignment(db_path, end_date=date(2026, 9, 30))
    app = _open_assignment(_new_app(), assignment_id)
    suffix = f"{assignment_id}_new"

    app.date_input(key=f"work_rate_effective_from_{suffix}").set_value(date(2026, 10, 1))
    app.text_input(key=f"work_rate_unit_price_{suffix}").set_value("700000")
    app.button(key=f"work_rate_submit_{suffix}").click()
    app = app.run()

    assert any("参画の開始日〜終了日の中" in error.value for error in app.error)
    assert _count(db_path, "rate_history") == 0


def test_register_rate_with_non_integer_price_shows_error(db_path: Path) -> None:
    assignment_id = _create_assignment(db_path)
    app = _open_assignment(_new_app(), assignment_id)
    suffix = f"{assignment_id}_new"

    app.date_input(key=f"work_rate_effective_from_{suffix}").set_value(date(2026, 4, 1))
    app.text_input(key=f"work_rate_unit_price_{suffix}").set_value("abc")
    app.button(key=f"work_rate_submit_{suffix}").click()
    app = app.run()

    assert any("0以上の整数" in error.value for error in app.error)
    assert _count(db_path, "rate_history") == 0


def _register_rate(db_path: Path, assignment_id: int) -> int:
    with open_connection(db_path) as connection:
        return register_rate_history(
            connection,
            assignment_id,
            RateHistoryInput(
                effective_from=date(2026, 4, 1), unit_price=700000, revision_basis=None, note=None
            ),
        )


def test_edit_rate(db_path: Path) -> None:
    assignment_id = _create_assignment(db_path)
    rate_id = _register_rate(db_path, assignment_id)
    app = _open_assignment(_new_app(), assignment_id)
    app.selectbox(key=f"work_rate_selected_{assignment_id}").set_value(rate_id)
    app = app.run()
    suffix = f"{assignment_id}_{rate_id}"
    assert app.text_input(key=f"work_rate_unit_price_{suffix}").value == "700000"

    app.text_input(key=f"work_rate_unit_price_{suffix}").set_value("750000")
    app.button(key=f"work_rate_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    with open_connection(db_path) as connection:
        unit_price = connection.execute("SELECT unit_price FROM rate_history").fetchone()[0]
    assert unit_price == 750000
    assert app.selectbox(key=f"work_rate_selected_{assignment_id}").value == rate_id


def test_delete_rate_after_confirmation(db_path: Path) -> None:
    assignment_id = _create_assignment(db_path)
    rate_id = _register_rate(db_path, assignment_id)
    app = _open_assignment(_new_app(), assignment_id)
    app.selectbox(key=f"work_rate_selected_{assignment_id}").set_value(rate_id)
    app = app.run()

    app.button(key=f"work_rate_delete_{assignment_id}_{rate_id}").click()
    app = app.run()
    assert _count(db_path, "rate_history") == 1

    app.button(key="work_rate_delete_confirm_confirm").click()
    app = app.run()

    assert not app.exception
    assert any("単価を削除しました" in toast.value for toast in app.toast)
    assert _count(db_path, "rate_history") == 0
    # 削除した単価の選択は、新規登録に戻る。
    assert app.selectbox(key=f"work_rate_selected_{assignment_id}").value is None


def test_cancel_rate_deletion_keeps_rate(db_path: Path) -> None:
    assignment_id = _create_assignment(db_path)
    rate_id = _register_rate(db_path, assignment_id)
    app = _open_assignment(_new_app(), assignment_id)
    app.selectbox(key=f"work_rate_selected_{assignment_id}").set_value(rate_id)
    app = app.run()

    app.button(key=f"work_rate_delete_{assignment_id}_{rate_id}").click()
    app = app.run()
    app.button(key="work_rate_delete_confirm_cancel").click()
    app = app.run()

    assert not app.exception
    assert _count(db_path, "rate_history") == 1


def test_shrinking_assignment_period_past_rate_shows_error(db_path: Path) -> None:
    assignment_id = _create_assignment(db_path)
    _register_rate(db_path, assignment_id)
    app = _open_assignment(_new_app(), assignment_id)
    suffix = f"1_{assignment_id}"

    app.date_input(key=f"work_assignment_start_{suffix}").set_value(date(2026, 5, 1))
    app.button(key=f"work_assignment_submit_{suffix}").click()
    app = app.run()

    assert any("先に単価を直してください" in error.value for error in app.error)


# ---- 稼働期間タブ ----


def _fill_period(
    app: AppTest, start: date, status: str = "WAIT_FULL", end: date | None = None
) -> None:
    app.date_input(key="work_period_start_1_new").set_value(start)
    if end is not None:
        app.date_input(key="work_period_end_1_new").set_value(end)
    app.selectbox(key="work_period_status_1_new").set_value(status)


def test_period_tab_defaults_to_current_half(db_path: Path) -> None:
    app = _new_app()

    assert not app.exception
    assert app.selectbox(key="work_period_half").value == current_fiscal_half(date.today())
    assert app.selectbox(key="work_period_employee").value == 1


def test_register_period_updates_grid_and_clears_inputs(db_path: Path) -> None:
    app = _new_app()
    _fill_period(app, date(2026, 10, 1))
    app = app.run()
    app.button(key="work_period_submit_1_new").click()
    app = app.run()

    assert not app.exception
    assert any("稼働期間を登録しました" in toast.value for toast in app.toast)
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT start_date, end_date, work_status_code, assignment_id, billing_ratio_bp "
            "FROM work_period"
        ).fetchone()
    assert row == ("2026-10-01", None, "WAIT_FULL", None, 10000)
    assert app.date_input(key="work_period_start_1_new").value is None


def test_assignment_field_is_shown_only_for_billable_status(db_path: Path) -> None:
    app = _new_app()
    app.selectbox(key="work_period_status_1_new").set_value("WAIT_FULL")
    app = app.run()
    assert not [box for box in app.selectbox if box.key == "work_period_assignment_1_new"]

    app.selectbox(key="work_period_status_1_new").set_value("DEV")
    app = app.run()

    assert app.selectbox(key="work_period_assignment_1_new").value is None


def test_tc_scn_014_billable_status_without_assignment_shows_error(db_path: Path) -> None:
    app = _new_app()
    _fill_period(app, date(2026, 10, 1), status="DEV")
    app = app.run()
    app.button(key="work_period_submit_1_new").click()
    app = app.run()

    assert any("参画を選んでください" in error.value for error in app.error)
    assert _count(db_path, "work_period") == 0


def test_register_billable_period_with_assignment(db_path: Path) -> None:
    assignment_id = _create_assignment(db_path)
    app = _new_app()
    _fill_period(app, date(2026, 10, 1), status="DEV")
    app = app.run()
    app.selectbox(key="work_period_assignment_1_new").set_value(assignment_id)
    app.text_input(key="work_period_ratio_1_new").set_value("70.5")
    app.button(key="work_period_submit_1_new").click()
    app = app.run()

    assert not app.exception
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT work_status_code, assignment_id, billing_ratio_bp FROM work_period"
        ).fetchone()
    assert row == ("DEV", assignment_id, 7050)


def test_tc_scn_013_overlapping_period_shows_error(db_path: Path) -> None:
    app = _new_app()
    for start in (date(2026, 10, 1), date(2026, 11, 1)):
        _fill_period(app, start)
        app = app.run()
        app.button(key="work_period_submit_1_new").click()
        app = app.run()

    assert any("期間が重なっています" in error.value for error in app.error)
    assert _count(db_path, "work_period") == 1


def test_edit_period(db_path: Path) -> None:
    app = _new_app()
    _fill_period(app, date(2026, 10, 1))
    app = app.run()
    app.button(key="work_period_submit_1_new").click()
    app = app.run()
    with open_connection(db_path) as connection:
        period_id = connection.execute("SELECT id FROM work_period").fetchone()[0]

    app.selectbox(key="work_period_selected_1").set_value(period_id)
    app = app.run()
    suffix = f"1_{period_id}"
    assert app.text_input(key=f"work_period_ratio_{suffix}").value == "100.00"
    app.date_input(key=f"work_period_end_{suffix}").set_value(date(2026, 10, 31))
    app.button(key=f"work_period_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    with open_connection(db_path) as connection:
        end_date = connection.execute("SELECT end_date FROM work_period").fetchone()[0]
    assert end_date == "2026-10-31"
    assert app.selectbox(key="work_period_selected_1").value == period_id
