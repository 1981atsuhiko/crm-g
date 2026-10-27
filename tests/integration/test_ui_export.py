"""出力画面の試験（`SCN-080`、`TC-FUN-019`〜`TC-FUN-021`）。"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.business_calendar import create_year_calendar
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations
from crm_g.ui.session import EXPORT_STATE_KEY

_VIEW_PATH = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "export.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-11-30T00:00:00.000000Z"


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """2026-11 は締め前、2026-10 は締め済みの計算結果がある。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "crm-g.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, data_dir / "backups")
        connection.execute(
            "INSERT INTO employee (id, email, name, enrollment_status, created_at, updated_at) "
            "VALUES (1, 'e1@example.com', 'A', '在籍', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        for year_month, closed_at in (("2026-11", None), ("2026-10", _TIMESTAMP)):
            connection.execute(
                "INSERT INTO monthly_payroll (employee_id, year_month, monthly_salary, "
                "paid_amount, deduction, check_result, calculated_at, is_closed, closed_at, "
                "created_at, updated_at) VALUES (1, ?, 300000, 300000, 0, 'OK', ?, ?, ?, ?, ?)",
                (
                    year_month,
                    _TIMESTAMP,
                    int(closed_at is not None),
                    closed_at,
                    _TIMESTAMP,
                    _TIMESTAMP,
                ),
            )
        connection.commit()
    return path


def _open(year: int, month: int) -> AppTest:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    app.selectbox(key="export_month_year").set_value(year)
    app.selectbox(key="export_month_month").set_value(month)
    return app.run()


def _prepare(app: AppTest) -> AppTest:
    app.button(key="export_prepare").click()
    return app.run()


def test_tc_fun_019_open_month_shows_status_and_prepares_csv(db_path: Path) -> None:
    app = _open(2026, 11)

    assert any("締め前の月（未確定の値）" in info.value for info in app.info)
    assert any("機微情報を含む場合があります" in warning.value for warning in app.warning)
    assert not app.get("download_button")

    app = _prepare(app)

    assert not app.exception
    assert len(app.get("download_button")) == 1
    prepared = app.session_state[EXPORT_STATE_KEY]
    assert prepared["file_name"] == "人事連携_2026-11.csv"
    assert prepared["content"].decode("utf-8-sig").startswith("氏名,対象ステータス,")


def test_tc_fun_019_closed_month_has_no_status(db_path: Path) -> None:
    app = _prepare(_open(2026, 10))

    assert not any("締め前の月" in info.value for info in app.info)
    assert len(app.get("download_button")) == 1


def test_tc_fun_021_failure_to_log_hides_download(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_export BEFORE INSERT ON operation_log "
            "WHEN NEW.operation = '出力' BEGIN SELECT RAISE(ABORT, 'failed'); END"
        )
        connection.commit()

    app = _prepare(_open(2026, 11))

    assert app.error
    assert not app.get("download_button")
    assert EXPORT_STATE_KEY not in app.session_state


def test_month_without_results_shows_error(db_path: Path) -> None:
    app = _prepare(_open(2026, 9))

    assert any("2026/09 の給与の計算結果がありません" in error.value for error in app.error)
    assert not any("締め前の月" in info.value for info in app.info)
    assert not app.get("download_button")


def test_changing_the_condition_discards_the_prepared_csv(db_path: Path) -> None:
    app = _prepare(_open(2026, 11))
    app.selectbox(key="export_month_month").set_value(10)
    app = app.run()

    assert not app.get("download_button")
    assert EXPORT_STATE_KEY not in app.session_state


@pytest.mark.parametrize(
    "kind",
    ["社員一覧", "昇給一覧", "月次給与計算の一覧", "稼働の一覧", "売上の集計"],
)
def test_tc_fun_020_each_list_can_be_exported(db_path: Path, kind: str) -> None:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    app.selectbox(key="export_kind").set_value(kind)
    app = app.run()
    if kind == "月次給与計算の一覧":
        app.selectbox(key="export_month_year").set_value(2026)
        app.selectbox(key="export_month_month").set_value(11)
        app = app.run()

    app = _prepare(app)

    assert not app.exception
    assert len(app.get("download_button")) == 1
    file_name = app.session_state[EXPORT_STATE_KEY]["file_name"]
    # ファイル名は `種類_条件.csv`。氏名（A）・金額を含まない。
    assert file_name.startswith(f"{kind}_")
    assert file_name.endswith(".csv")
    assert "A" not in file_name.removeprefix(f"{kind}_")
    assert "300000" not in file_name


def test_bonus_list_without_calendar_shows_error_then_exports(db_path: Path) -> None:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    app.selectbox(key="export_kind").set_value("賞与の一覧")
    app = app.run()
    app.selectbox(key="export_bonus_half").set_value((2026, 1))
    app = _prepare(app.run())

    assert any("営業日カレンダーがありません" in error.value for error in app.error)
    assert not app.get("download_button")

    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)
    app = _prepare(app)

    assert app.session_state[EXPORT_STATE_KEY]["file_name"] == "賞与の一覧_2026-H1.csv"


def test_employee_list_without_statuses_cannot_be_prepared(db_path: Path) -> None:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    app.selectbox(key="export_kind").set_value("社員一覧")
    app = app.run()
    app.multiselect(key="export_enrollment_statuses").set_value([])
    app = app.run()

    assert app.button(key="export_prepare").disabled
    assert any("1つ以上選んでください" in info.value for info in app.info)


def test_changing_the_kind_discards_the_prepared_csv(db_path: Path) -> None:
    app = _prepare(_open(2026, 11))
    app.selectbox(key="export_kind").set_value("社員一覧")
    app = app.run()

    assert not app.get("download_button")
    assert EXPORT_STATE_KEY not in app.session_state


def test_employee_statuses_are_ordered_by_definition(db_path: Path) -> None:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    app.selectbox(key="export_kind").set_value("社員一覧")
    app = app.run()
    app.multiselect(key="export_enrollment_statuses").set_value(["退職予定", "在籍"])
    app = _prepare(app.run())

    assert app.session_state[EXPORT_STATE_KEY]["file_name"] == "社員一覧_在籍・退職予定.csv"


def test_tc_fun_021_failure_to_log_a_list_hides_download(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_export BEFORE INSERT ON operation_log "
            "WHEN NEW.operation = '出力' BEGIN SELECT RAISE(ABORT, 'failed'); END"
        )
        connection.commit()
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    app.selectbox(key="export_kind").set_value("昇給一覧")
    app = _prepare(app.run())

    assert app.error
    assert not app.get("download_button")


@pytest.mark.parametrize(
    ("kind", "unit_key", "unit"),
    [
        ("昇給一覧", "export_raise_unit", "通期"),
        ("売上の集計", "export_sales_unit", "期別"),
    ],
)
def test_other_period_units_can_be_exported(
    db_path: Path, kind: str, unit_key: str, unit: str
) -> None:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    app.selectbox(key="export_kind").set_value(kind)
    app = app.run()
    if kind == "昇給一覧":
        app.radio(key=unit_key).set_value(unit)
    else:
        app.segmented_control(key=unit_key).set_value(unit)
    app = _prepare(app.run())

    assert not app.exception
    assert (
        "通期" in app.session_state[EXPORT_STATE_KEY]["file_name"]
        or "期別_全期" in (app.session_state[EXPORT_STATE_KEY]["file_name"])
    )
