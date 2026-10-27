"""賞与画面の試験（`SCN-040`、`TC-FUN-016`、`TC-FUN-017`、`TC-SCN-017`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.recalculation import recalculate_month
from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.application.work_period import WorkPeriodInput, create_work_period
from crm_g.domain.period import current_fiscal_half, half_code, previous_half
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "bonus.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-25T00:00:00.000000Z"
_HALF = (2026, 1)
_SUFFIX = "2026-H1_1"


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """P：通常評価制度、Q：年俸制、R：2026-09-30 退職（TC-FUN-016）。掛け率は A だけ登録。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "crm-g.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, data_dir / "backups")
        for employee_id, name, retire in ((1, "P", None), (2, "Q", None), (3, "R", "2026-09-30")):
            connection.execute(
                "INSERT INTO employee (id, email, name, enrollment_status, join_date, "
                "retire_date, created_at, updated_at) "
                "VALUES (?, ?, ?, '在籍', '2020-04-01', ?, ?, ?)",
                (employee_id, f"e{employee_id}@example.com", name, retire, _TIMESTAMP, _TIMESTAMP),
            )
        connection.execute(
            "INSERT INTO bonus_rate (eval_half, eval_rank, rate_bp, created_at, updated_at) "
            "VALUES ('2026-H1', 'A', 12500, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
        create_year_calendar(connection, 2026)
        for employee_id in (1, 2, 3):
            annual = employee_id == 2
            register_salary_history(
                connection,
                employee_id,
                SalaryHistoryInput(
                    effective_from=date(2026, 4, 1),
                    pay_system="年俸制" if annual else "通常評価制度",
                    employment_type="正社員",
                    grade=None,
                    annual_salary=3600000 if annual else None,
                    revision_type="年俸改定（4月）" if annual else "定期（4月）",
                    note=None,
                    items=(SalaryItemInput("BASE_SALARY", 300000),),
                ),
            )
        create_work_period(
            connection,
            1,
            WorkPeriodInput(
                start_date=date(2026, 4, 1),
                end_date=None,
                work_status_code="WAIT_FULL",
                assignment_id=None,
                billing_ratio_percent="100",
                note=None,
            ),
        )
        recalculate_month(connection, "2026-10")
        connection.commit()
    return path


def _open(half: tuple[int, int] = _HALF) -> AppTest:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    app.selectbox(key="bonus_half").set_value(half)
    return app.run()


def _rows(app: AppTest) -> list[dict[str, object]]:
    records: list[dict[str, object]] = app.dataframe[0].value.to_dict("records")
    return records


def _evaluate(app: AppTest, rank: str) -> AppTest:
    app.selectbox(key=f"bonus_eval_rank_{_SUFFIX}").set_value(rank)
    app.text_input(key=f"bonus_eval_evaluator_{_SUFFIX}").set_value("上長")
    app.button(key=f"bonus_eval_submit_{_SUFFIX}").click()
    return app.run()


def _fill_manual(app: AppTest, suffix: str = "2026-H1") -> None:
    app.text_input(key=f"bonus_manual_base_amount_{suffix}").set_value("300000")
    app.text_input(key=f"bonus_manual_rate_{suffix}").set_value("1.25")
    app.text_input(key=f"bonus_manual_amount_{suffix}").set_value("375000")


def test_tc_fun_016_only_targets_are_listed(db_path: Path) -> None:
    app = _open()

    assert not app.exception
    assert [row["氏名"] for row in _rows(app)] == ["P"]
    assert any("支給月：2026-10" in markdown.value for markdown in app.markdown)


def test_tc_fun_017_ranks_are_limited_and_missing_rate_is_listed(db_path: Path) -> None:
    app = _open()

    assert app.selectbox(key=f"bonus_eval_rank_{_SUFFIX}").options == [
        "（未入力）",
        "S",
        "A",
        "B",
        "C",
        "D",
    ]
    app = _evaluate(app, "B")

    assert any("評価を保存しました" in toast.value for toast in app.toast)
    assert _rows(app)[0]["評価ランク"] == "B"
    assert _rows(app)[0]["掛け率（倍）"] == "－"
    assert app.dataframe[1].value.to_dict("records") == [{"氏名": "P"}]


def test_tc_scn_017_confirmed_row_is_locked_and_reflected_in_cost(db_path: Path) -> None:
    app = _evaluate(_open(), "A")
    app.button(key="bonus_calculate").click()
    app = app.run()
    assert _rows(app)[0]["賞与額（円）"] == "375,000"

    app.button(key="bonus_confirm").click()
    app = app.run()
    app.button(key="bonus_confirm_dialog_confirm").click()
    app = app.run()

    assert not app.exception
    assert any("1人の賞与を確定しました" in toast.value for toast in app.toast)
    assert any("1か月分（2026-10）を再計算しました" in info.value for info in app.info)
    assert _rows(app)[0]["確定"] == "○"
    assert not [box for box in app.selectbox if box.key == "bonus_eval_employee_2026-H1"]
    with open_connection(db_path) as connection:
        assert connection.execute(
            "SELECT cost_bonus FROM monthly_pl WHERE employee_id = 1 AND year_month = '2026-10'"
        ).fetchone() == (375000,)


def test_19th_period_has_only_the_manual_form(db_path: Path) -> None:
    app = _open((2025, 2))

    assert not app.exception
    assert any("19期の半期は" in info.value for info in app.info)
    assert not [button for button in app.button if button.key == "bonus_calculate"]
    assert [box for box in app.selectbox if box.key == "bonus_manual_employee_2025-H2"]


def test_manual_bonus_registration(db_path: Path) -> None:
    app = _open()
    _fill_manual(app)
    app.button(key="bonus_manual_submit_2026-H1").click()
    app = app.run()

    assert not app.exception
    assert any("支給済みの賞与を登録しました" in toast.value for toast in app.toast)
    assert _rows(app)[0]["確定"] == "○"


def test_manual_bonus_for_non_target_shows_error(db_path: Path) -> None:
    app = _open()
    app.selectbox(key="bonus_manual_employee_2026-H1").set_value(2)
    _fill_manual(app)
    app.button(key="bonus_manual_submit_2026-H1").click()
    app = app.run()

    assert any("賞与の対象者ではありません" in error.value for error in app.error)


def test_missing_base_month_calendar_shows_error(db_path: Path) -> None:
    app = _open((2027, 1))

    assert not app.exception
    assert any("営業日カレンダーがありません" in error.value for error in app.error)


def test_default_half_is_the_previous_half(db_path: Path) -> None:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()

    assert app.selectbox(key="bonus_half").value == previous_half(
        *current_fiscal_half(date.today())
    )


def test_saved_evaluation_is_shown_in_the_form(db_path: Path) -> None:
    app = _evaluate(_open(), "A")
    app = _evaluate(app, "B")

    assert app.selectbox(key=f"bonus_eval_rank_{_SUFFIX}").value == "B"
    assert app.text_input(key=f"bonus_eval_evaluator_{_SUFFIX}").value == "上長"


def test_stale_calculation_blocks_confirmation(db_path: Path) -> None:
    app = _evaluate(_open(), "A")
    app.button(key="bonus_calculate").click()
    app = _evaluate(app.run(), "B")

    assert _rows(app)[0]["賞与額（円）"] == "要再計算"
    assert app.button(key="bonus_confirm").disabled
    assert any("要再計算" in warning.value for warning in app.warning)


def test_cancel_confirmation_keeps_bonus_open(db_path: Path) -> None:
    app = _evaluate(_open(), "A")
    app.button(key="bonus_calculate").click()
    app = app.run()
    app.button(key="bonus_confirm").click()
    app = app.run()
    app.button(key="bonus_confirm_dialog_cancel").click()
    app = app.run()

    assert not app.exception
    assert _rows(app)[0]["確定"] == "×"


def test_manual_entry_of_the_selected_employee_does_not_break_the_form(
    db_path: Path,
) -> None:
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO employee (id, email, name, enrollment_status, join_date, created_at, "
            "updated_at) VALUES (4, 'e4@example.com', 'S', '在籍', '2020-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
        register_salary_history(
            connection,
            4,
            SalaryHistoryInput(
                effective_from=date(2026, 4, 1),
                pay_system="通常評価制度",
                employment_type="正社員",
                grade=None,
                annual_salary=None,
                revision_type="定期（4月）",
                note=None,
                items=(SalaryItemInput("BASE_SALARY", 300000),),
            ),
        )
    app = _open()
    assert app.selectbox(key="bonus_eval_employee_2026-H1").value == 1

    _fill_manual(app)
    app.button(key="bonus_manual_submit_2026-H1").click()
    app = app.run()

    assert not app.exception
    assert app.selectbox(key="bonus_eval_employee_2026-H1").options == ["4：S"]
    assert app.text_input(key="bonus_manual_amount_2026-H1").value == ""


def test_non_integer_amount_shows_error(db_path: Path) -> None:
    app = _open()
    _fill_manual(app)
    app.text_input(key="bonus_manual_amount_2026-H1").set_value("abc")
    app.button(key="bonus_manual_submit_2026-H1").click()
    app = app.run()

    assert any("賞与額は、0 以上の整数" in error.value for error in app.error)


def test_view_log_is_recorded_once_per_half(db_path: Path) -> None:
    app = _open()
    app = app.run()
    app.selectbox(key="bonus_half").set_value((2025, 2))
    app = app.run()

    with open_connection(db_path) as connection:
        keys = connection.execute(
            "SELECT target_key FROM operation_log WHERE operation = '閲覧' "
            "AND target_table = 'bonus' ORDER BY id"
        ).fetchall()
    # 既定の半期（今日の1つ前）→ 2026-H1 → 再実行 → 2025-H2。同じ半期を続けて表示しても、
    # 記録は1回だけ（再実行では増えない）。
    default = half_code(*previous_half(*current_fiscal_half(date.today())))
    expected = [default] if default != "2026-H1" else []
    assert [key[0] for key in keys] == [*expected, "2026-H1", "2025-H2"]
