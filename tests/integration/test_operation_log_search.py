"""操作ログの照会の試験（`SCN-067`、`UID-080`、TC-SCN-022）。"""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from crm_g.application.operation_log import (
    OPERATION_LOG_DISPLAY_LIMIT,
    Operation,
    OperationLogRow,
    jst_date_range_to_utc,
    search_operation_logs,
)
from crm_g.infrastructure.db import open_connection, transaction
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_ALL_OPERATIONS = tuple(Operation)
_INSERT_SQL = (
    "INSERT INTO operation_log "
    "(occurred_at, operation, target_table, target_key, detail, before_value, after_value) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

# 1行分の値（occurred_at, operation, target_table, target_key, detail, before, after）。
LogValues = tuple[str, str, str, str | None, str | None, str | None, str | None]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """マイグレーションを適用した一時 DB を作る。"""
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def _insert_logs(db_path: Path, rows: list[LogValues]) -> None:
    """操作ログの行を、日時を指定して1つのトランザクションでまとめて入れる。"""
    with open_connection(db_path) as connection, transaction(connection):
        connection.executemany(_INSERT_SQL, rows)


def _log(occurred_at: str, operation: Operation = Operation.CREATE, key: str = "1") -> LogValues:
    """日時と操作の種類だけを変えた、操作ログの1行分の値を返す。"""
    return (occurred_at, operation.value, "employee", key, None, None, None)


def _occurred_at_list(rows: tuple[OperationLogRow, ...]) -> list[datetime]:
    """一覧の行の日時だけを、並び順のまま返す。"""
    return [row.occurred_at for row in rows]


def _sequential_logs(count: int) -> Iterator[LogValues]:
    """2026-10-15（日本時間）の中で、1秒ずつずらした行を `count` 件返す。"""
    for index in range(count):
        minutes, seconds = divmod(index, 60)
        yield _log(f"2026-10-15T00:{minutes:02d}:{seconds:02d}.000000Z", key=str(index))


def test_jst_date_range_to_utc_converts_to_half_open_utc_range() -> None:
    range_start, range_end = jst_date_range_to_utc(date(2026, 10, 1), date(2026, 10, 31))

    assert range_start == "2026-09-30T15:00:00.000000Z"
    assert range_end == "2026-10-31T15:00:00.000000Z"


def test_search_includes_jst_boundaries_and_excludes_outside(db_path: Path) -> None:
    """開始日の 0時（日本時間）は含み、終了日の翌日の 0時（日本時間）は含まない。"""
    _insert_logs(
        db_path,
        [
            _log("2026-09-30T14:59:59.999999Z", key="before-start"),
            _log("2026-09-30T15:00:00.000000Z", key="start"),
            _log("2026-10-31T14:59:59.999999Z", key="end"),
            _log("2026-10-31T15:00:00.000000Z", key="after-end"),
        ],
    )

    with open_connection(db_path) as connection:
        result = search_operation_logs(
            connection, date(2026, 10, 1), date(2026, 10, 31), _ALL_OPERATIONS
        )

    assert [row.target_key for row in result.rows] == ["end", "start"]
    assert result.exceeds_limit is False


def test_search_single_day_uses_the_jst_day(db_path: Path) -> None:
    """開始日と終了日が同じ日は、その日の日本時間の 0時〜24時を対象にする。"""
    _insert_logs(
        db_path,
        [
            _log("2026-10-04T14:59:59.999999Z", key="previous-day"),
            _log("2026-10-04T15:00:00.000000Z", key="day-start"),
            _log("2026-10-05T14:59:59.999999Z", key="day-end"),
            _log("2026-10-05T15:00:00.000000Z", key="next-day"),
        ],
    )

    with open_connection(db_path) as connection:
        result = search_operation_logs(
            connection, date(2026, 10, 5), date(2026, 10, 5), _ALL_OPERATIONS
        )

    assert [row.target_key for row in result.rows] == ["day-end", "day-start"]


def test_search_filters_by_operations(db_path: Path) -> None:
    _insert_logs(
        db_path,
        [
            _log("2026-10-10T01:00:00.000000Z", Operation.CREATE, key="create"),
            _log("2026-10-10T02:00:00.000000Z", Operation.UPDATE, key="update"),
            _log("2026-10-10T03:00:00.000000Z", Operation.AUTH_FAILURE, key="auth"),
        ],
    )

    with open_connection(db_path) as connection:
        result = search_operation_logs(
            connection,
            date(2026, 10, 1),
            date(2026, 10, 31),
            [Operation.CREATE, Operation.AUTH_FAILURE],
        )

    assert [row.target_key for row in result.rows] == ["auth", "create"]


def test_search_with_no_operations_returns_nothing(db_path: Path) -> None:
    _insert_logs(db_path, [_log("2026-10-10T01:00:00.000000Z")])

    with open_connection(db_path) as connection:
        result = search_operation_logs(connection, date(2026, 10, 1), date(2026, 10, 31), [])

    assert result.rows == ()
    assert result.exceeds_limit is False


def test_search_orders_newest_first_and_ties_by_id(db_path: Path) -> None:
    """同じ日時の行は、後から記録した行（`id` の大きい行）を先にする。"""
    _insert_logs(
        db_path,
        [
            _log("2026-10-10T01:00:00.000000Z", key="old"),
            _log("2026-10-10T03:00:00.000000Z", key="same-first"),
            _log("2026-10-10T03:00:00.000000Z", key="same-second"),
            _log("2026-10-10T02:00:00.000000Z", key="middle"),
        ],
    )

    with open_connection(db_path) as connection:
        result = search_operation_logs(
            connection, date(2026, 10, 1), date(2026, 10, 31), _ALL_OPERATIONS
        )

    assert [row.target_key for row in result.rows] == [
        "same-second",
        "same-first",
        "middle",
        "old",
    ]


def test_search_returns_values_as_stored(db_path: Path) -> None:
    """日時は UTC の datetime、変更前・変更後は JSON の文字列のまま返す（`SCD-022`）。"""
    _insert_logs(
        db_path,
        [
            (
                "2026-10-10T01:02:03.000000Z",
                Operation.UPDATE.value,
                "salary_history",
                "7",
                "base_salary",
                '{"base_salary": 300000}',
                '{"base_salary": 310000}',
            )
        ],
    )

    with open_connection(db_path) as connection:
        result = search_operation_logs(
            connection, date(2026, 10, 1), date(2026, 10, 31), _ALL_OPERATIONS
        )

    assert result.rows == (
        OperationLogRow(
            occurred_at=datetime(2026, 10, 10, 1, 2, 3, tzinfo=UTC),
            operation="更新",
            target_table="salary_history",
            target_key="7",
            detail="base_salary",
            before_value='{"base_salary": 300000}',
            after_value='{"base_salary": 310000}',
        ),
    )


def test_search_with_exactly_the_limit_does_not_exceed(db_path: Path) -> None:
    _insert_logs(db_path, list(_sequential_logs(OPERATION_LOG_DISPLAY_LIMIT)))

    with open_connection(db_path) as connection:
        result = search_operation_logs(
            connection, date(2026, 10, 1), date(2026, 10, 31), _ALL_OPERATIONS
        )

    assert len(result.rows) == OPERATION_LOG_DISPLAY_LIMIT
    assert result.exceeds_limit is False


def test_tc_scn_022_search_over_the_limit_returns_newest_limit_rows(db_path: Path) -> None:
    """1,000件を超えるときは、新しい順に1,000件を返し、超えたことを知らせる（`UID-080`）。"""
    _insert_logs(db_path, list(_sequential_logs(OPERATION_LOG_DISPLAY_LIMIT + 1)))

    with open_connection(db_path) as connection:
        result = search_operation_logs(
            connection, date(2026, 10, 1), date(2026, 10, 31), _ALL_OPERATIONS
        )

    assert len(result.rows) == OPERATION_LOG_DISPLAY_LIMIT
    assert result.exceeds_limit is True
    occurred_at_list = _occurred_at_list(result.rows)
    assert occurred_at_list == sorted(occurred_at_list, reverse=True)
    # 最も古い1件（key="0"）だけが、表示の対象から外れる。
    assert result.rows[0].target_key == str(OPERATION_LOG_DISPLAY_LIMIT)
    assert result.rows[-1].target_key == "1"


def test_search_rejects_start_after_end(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(ValueError, match="開始日"):
        search_operation_logs(connection, date(2026, 10, 2), date(2026, 10, 1), _ALL_OPERATIONS)


def test_search_does_not_record_view(db_path: Path) -> None:
    """照会しても `閲覧` を記録しない（`UID-060`。ユーザー承認 2026-10-27）。"""
    _insert_logs(db_path, [_log("2026-10-10T01:00:00.000000Z")])

    with open_connection(db_path) as connection:
        search_operation_logs(connection, date(2026, 10, 1), date(2026, 10, 31), _ALL_OPERATIONS)
        count = connection.execute("SELECT COUNT(*) FROM operation_log").fetchone()[0]

    assert count == 1
