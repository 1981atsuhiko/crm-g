"""操作ログ画面の試験（`SCN-067`、TC-SCN-022）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.operation_log import OPERATION_LOG_DISPLAY_LIMIT, Operation
from crm_g.infrastructure.db import open_connection, transaction
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "operation_log.py"
)
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30
_INSERT_SQL = (
    "INSERT INTO operation_log "
    "(occurred_at, operation, target_table, target_key, detail, before_value, after_value) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)
# TC-SCN-022：1,000件を超える操作ログを用意する（`登録` を上限より多く、`更新` を2件）。
_CREATE_LOG_COUNT = OPERATION_LOG_DISPLAY_LIMIT + 50
_UPDATE_LOG_COUNT = 2

# 1行分の値（occurred_at, operation, target_table, target_key, detail, before, after）。
LogValues = tuple[str, str, str, str | None, str | None, str | None, str | None]


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """`get_app_paths()` が一時ディレクトリを指すようにし、操作ログを用意した DB を作る。

    `登録` は 2026-10-15（日本時間）の中に1秒ずつ、`更新` は 2026-10-20 に入れる。
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "crm-g.db"
    create_logs: list[LogValues] = []
    for index in range(_CREATE_LOG_COUNT):
        minutes, seconds = divmod(index, 60)
        create_logs.append(
            (
                f"2026-10-15T00:{minutes:02d}:{seconds:02d}.000000Z",
                Operation.CREATE.value,
                "employee",
                str(index),
                None,
                None,
                '{"name": "A"}',
            )
        )
    update_logs: list[LogValues] = [
        (
            # 2026-10-20 09:30（日本時間）
            "2026-10-20T00:30:00.000000Z",
            Operation.UPDATE.value,
            "salary_history",
            "7",
            "base_salary",
            '{"base_salary": 300000}',
            '{"base_salary": 310000}',
        ),
        (
            # 2026-10-20 23:59（日本時間）。UTC では同じ日の 14:59。
            "2026-10-20T14:59:00.000000Z",
            Operation.UPDATE.value,
            "employee",
            "8",
            None,
            '{"name": "B"}',
            '{"name": "C"}',
        ),
    ]
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, data_dir / "backups")
        with transaction(connection):
            connection.executemany(_INSERT_SQL, create_logs + update_logs)
    return path


def _new_app() -> AppTest:
    """画面を初回実行した `AppTest` を返す。"""
    return AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def _set_period(app: AppTest, start_date: date, end_date: date) -> AppTest:
    """期間を設定して、再実行した結果を返す。"""
    app.date_input(key="operation_log_start_date").set_value(start_date)
    app.date_input(key="operation_log_end_date").set_value(end_date)
    return app.run()


def _log_count(db_path: Path) -> int:
    """操作ログの総件数を返す。"""
    with open_connection(db_path) as connection:
        count: int = connection.execute("SELECT COUNT(*) FROM operation_log").fetchone()[0]
    return count


def _warnings(app: AppTest) -> list[str]:
    """画面に出た警告の文言を返す。"""
    return [warning.value for warning in app.warning]


def test_shows_notice_and_default_filters(db_path: Path) -> None:
    app = _new_app()

    assert [title.value for title in app.title] == ["操作ログ"]
    assert any("機微情報" in message for message in _warnings(app))
    today = date.today()
    assert app.date_input(key="operation_log_start_date").value == today.replace(day=1)
    assert app.date_input(key="operation_log_end_date").value == today
    # 操作の種類は `Operation` の全値で、既定はすべてを選ぶ。
    operations = app.multiselect(key="operation_log_operations")
    assert operations.options == [operation.value for operation in Operation]
    assert operations.value == [operation.value for operation in Operation]
    assert not app.exception


def test_tc_scn_022_over_the_limit_shows_narrow_down_message(db_path: Path) -> None:
    app = _set_period(_new_app(), date(2026, 10, 1), date(2026, 10, 31))

    assert any("絞り込んでください" in message for message in _warnings(app))
    table = app.dataframe[0].value
    assert len(table) == OPERATION_LOG_DISPLAY_LIMIT
    # 新しい順：先頭は 2026-10-20 23:59（日本時間）の `更新`。
    assert table.iloc[0]["日時"] == "2026/10/20 23:59"
    assert table.iloc[0]["操作"] == "更新"
    assert not app.exception


def test_tc_scn_022_narrowing_by_operation_hides_message(db_path: Path) -> None:
    app = _set_period(_new_app(), date(2026, 10, 1), date(2026, 10, 31))
    app.multiselect(key="operation_log_operations").set_value([Operation.UPDATE.value])
    app.run()

    assert not any("絞り込んでください" in message for message in _warnings(app))
    assert app.dataframe[0].value.to_dict("records") == [
        {
            "日時": "2026/10/20 23:59",
            "操作": "更新",
            "対象テーブル": "employee",
            "対象キー": "8",
            "詳細": "－",
            "変更前": '{"name": "B"}',
            "変更後": '{"name": "C"}',
        },
        {
            "日時": "2026/10/20 09:30",
            "操作": "更新",
            "対象テーブル": "salary_history",
            "対象キー": "7",
            "詳細": "base_salary",
            "変更前": '{"base_salary": 300000}',
            "変更後": '{"base_salary": 310000}',
        },
    ]
    assert not app.exception


def test_tc_scn_022_narrowing_by_period_hides_message(db_path: Path) -> None:
    """期間を 2026-10-20 だけにすると、`更新` の2件だけになる（日本時間の日付で絞り込む）。"""
    app = _set_period(_new_app(), date(2026, 10, 20), date(2026, 10, 20))

    assert not any("絞り込んでください" in message for message in _warnings(app))
    assert len(app.dataframe[0].value) == _UPDATE_LOG_COUNT
    assert not app.exception


def test_no_matching_logs_shows_status(db_path: Path) -> None:
    app = _set_period(_new_app(), date(2026, 9, 1), date(2026, 9, 30))

    assert [info.value for info in app.info] == ["該当する操作ログがありません。"]
    assert len(app.dataframe) == 0
    assert not app.exception


def test_start_after_end_shows_error(db_path: Path) -> None:
    app = _set_period(_new_app(), date(2026, 10, 31), date(2026, 10, 1))

    assert [error.value for error in app.error] == ["開始日は、終了日以前の日付にしてください。"]
    assert len(app.dataframe) == 0
    assert not app.exception


def test_viewing_and_filtering_do_not_record_view(db_path: Path) -> None:
    """画面を開いても、絞り込みを変えても、`閲覧` を記録しない（`UID-060`）。"""
    before_count = _log_count(db_path)

    app = _set_period(_new_app(), date(2026, 10, 1), date(2026, 10, 31))
    app.multiselect(key="operation_log_operations").set_value([Operation.CREATE.value])
    app.run()

    assert _log_count(db_path) == before_count
    assert not app.exception
