"""売上画面の見込みの試験（`SCN-051`、`TC-FUN-013`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "sales.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """社員 A（在籍）、社員 Z（退職。新規の入力の対象外）、社員 A（id=3。同じ氏名の在籍社員）。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "crm-g.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, data_dir / "backups")
        for employee_id, name, status in ((1, "A", "在籍"), (2, "Z", "退職"), (3, "A", "在籍")):
            connection.execute(
                "INSERT INTO employee (id, email, name, enrollment_status, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (employee_id, f"e{employee_id}@example.com", name, status, _TIMESTAMP, _TIMESTAMP),
            )
        connection.commit()
    return path


def _open() -> AppTest:
    """見込みの対象月の範囲を、2026-11〜2026-12 にして開く（今日の日付に依らないため）。"""
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    for key, value in (
        ("sales_forecast_from_year", 2026),
        ("sales_forecast_from_month", 11),
        ("sales_forecast_to_year", 2026),
        ("sales_forecast_to_month", 12),
    ):
        app.selectbox(key=key).set_value(value)
        app = app.run()
    return app


def _enter(app: AppTest, confidence: str, unit_price: str, ratio: str) -> AppTest:
    app.selectbox(key="sales_forecast_month_new_year").set_value(2026)
    app.selectbox(key="sales_forecast_month_new_month").set_value(11)
    app.selectbox(key="sales_forecast_confidence_new").set_value(confidence)
    app.text_input(key="sales_forecast_unit_price_new").set_value(unit_price)
    app.text_input(key="sales_forecast_ratio_new").set_value(ratio)
    app.button(key="sales_forecast_submit_new").click()
    return app.run()


def _tables(app: AppTest) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    forecast_tab = app.tabs[1]
    by_confidence: list[dict[str, object]] = forecast_tab.dataframe[0].value.to_dict("records")
    rows: list[dict[str, object]] = (
        forecast_tab.dataframe[1].value.to_dict("records")
        if len(forecast_tab.dataframe) > 1
        else []
    )
    return by_confidence, rows


def test_tc_fun_013_register_and_update(db_path: Path) -> None:
    app = _enter(_open(), "高", "600000", "70")

    assert not app.exception
    assert any("見込みを登録しました" in toast.value for toast in app.toast)
    by_confidence, rows = _tables(app)
    assert rows == [
        {
            "氏名": "A",
            "年月": "2026/11",
            "確度": "高",
            "見込み単価（円）": "600,000",
            "見込み稼働率": "70.00%",
            "見込み売上（円）": "420,000",
        }
    ]
    assert by_confidence[1] == {"確度": "高", "人数": 1, "見込み売上（円）": "420,000"}

    # 同じ社員・年月で、もう一度入力すると、更新として扱われる。
    app = _enter(app, "確定", "600000", "70")

    assert any("見込みを更新しました" in toast.value for toast in app.toast)
    by_confidence, rows = _tables(app)
    assert [row["確度"] for row in rows] == ["確定"]
    assert by_confidence[0] == {"確度": "確定", "人数": 1, "見込み売上（円）": "420,000"}


def test_tc_fun_013_blank_unit_price_is_not_totaled(db_path: Path) -> None:
    app = _enter(_open(), "低", "", "70")

    by_confidence, rows = _tables(app)
    assert rows[0]["見込み売上（円）"] == "－"
    assert rows[0]["見込み単価（円）"] == "－"
    assert by_confidence[2] == {"確度": "低", "人数": 1, "見込み売上（円）": "0"}


def test_edit_existing_forecast(db_path: Path) -> None:
    app = _enter(_open(), "高", "600000", "70")
    app.selectbox(key="sales_forecast_choice").set_value("1:2026-11")
    app = app.run()

    suffix = "1_2026-11"
    # 社員・年月は主キーのため、編集では表示だけにする。
    assert not [box for box in app.selectbox if box.key == f"sales_forecast_employee_{suffix}"]
    assert any("社員：1：A　年月：2026/11" in text.value for text in app.markdown)
    assert app.text_input(key=f"sales_forecast_unit_price_{suffix}").value == "600000"
    assert app.text_input(key=f"sales_forecast_ratio_{suffix}").value == "70"
    app.text_input(key=f"sales_forecast_ratio_{suffix}").set_value("50.5")
    app.button(key=f"sales_forecast_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    _, rows = _tables(app)
    assert (rows[0]["見込み稼働率"], rows[0]["見込み売上（円）"]) == ("50.50%", "303,000")
    # 保存のあとは、新規の入力に戻る。
    assert app.selectbox(key="sales_forecast_choice").value == "（新規）"


def test_invalid_input_shows_error(db_path: Path) -> None:
    app = _enter(_open(), "高", "abc", "70")

    assert any("見込み単価は、0 以上の整数" in error.value for error in app.error)
    assert _tables(app)[1] == []


def test_only_enrolled_employees_can_be_selected(db_path: Path) -> None:
    app = _open()

    assert app.selectbox(key="sales_forecast_employee_new").options == ["1：A", "3：A"]


def test_employees_with_the_same_name_are_kept_apart(db_path: Path) -> None:
    app = _open()
    app.selectbox(key="sales_forecast_employee_new").set_value(1)
    app = _enter(app, "高", "600000", "70")

    with open_connection(db_path) as connection:
        saved = connection.execute("SELECT employee_id FROM forecast").fetchall()
    assert saved == [(1,)]
    assert app.selectbox(key="sales_forecast_choice").options == ["（新規）", "1：A　2026/11"]


def test_forecast_of_retired_employee_can_be_edited(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO forecast (employee_id, year_month, confidence, created_at, updated_at) "
            "VALUES (2, '2026-11', '低', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.execute("UPDATE employee SET enrollment_status = '退職' WHERE id IN (1, 3)")
        connection.commit()
    app = _open()

    # 入力できる社員がいないため、新規はなく、既存の見込みの編集だけになる。
    assert app.selectbox(key="sales_forecast_choice").options == ["2：Z　2026/11"]
    app.selectbox(key="sales_forecast_confidence_2_2026-11").set_value("待機")
    app.button(key="sales_forecast_submit_2_2026-11").click()
    app = app.run()

    assert any("見込みを更新しました" in toast.value for toast in app.toast)
    assert [row["確度"] for row in _tables(app)[1]] == ["待機"]


def test_saving_unchanged_values_shows_no_change(db_path: Path) -> None:
    app = _enter(_open(), "高", "600000", "70")
    app.selectbox(key="sales_forecast_choice").set_value("1:2026-11")
    app = app.run()
    app.button(key="sales_forecast_submit_1_2026-11").click()
    app = app.run()

    assert any("見込みに変更はありません" in toast.value for toast in app.toast)


def test_default_range_and_month(db_path: Path) -> None:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    today = date.today()
    end = today.year * 12 + today.month - 1 + 5
    following = today.year * 12 + today.month

    assert (
        app.selectbox(key="sales_forecast_from_year").value,
        app.selectbox(key="sales_forecast_from_month").value,
    ) == (today.year, today.month)
    assert (
        app.selectbox(key="sales_forecast_to_year").value,
        app.selectbox(key="sales_forecast_to_month").value,
    ) == (end // 12, end % 12 + 1)
    assert (
        app.selectbox(key="sales_forecast_month_new_year").value,
        app.selectbox(key="sales_forecast_month_new_month").value,
    ) == (following // 12, following % 12 + 1)


def test_reversed_range_shows_error(db_path: Path) -> None:
    app = _open()
    app.selectbox(key="sales_forecast_from_month").set_value(12)
    app.selectbox(key="sales_forecast_to_month").set_value(11)
    app = app.run()

    assert any("終了月は、開始月と同じか" in error.value for error in app.error)
