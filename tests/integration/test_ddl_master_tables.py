"""DDL（マスタテーブル）の試験（TC-DBD-001〜006、008）。

実際の DDL/ 配下の連番SQLに対して実施する（実装計画書5.3節：PH-04は実際のDDL/で
確認する）。TC-DBD-007（外部キーの削除）は、PH-04-2・03で実施する。TC-DBD-017
（初期データ）はPH-04-4で実施する。work_status には初期データ（PH-04-4）が
既に入っているため、試験では初期データと衝突しないコード（TEST_DEV）を使う。
"""

import sqlite3
from pathlib import Path

import pytest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-21T00:00:00.000000Z"

# operation_log・schema_migration は、created_at・updated_at を持たない（DBD-011）。
_TABLES_WITHOUT_COMMON_COLUMNS = {"schema_migration", "operation_log"}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def _user_table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [row[0] for row in rows]


def test_tc_dbd_001_all_tables_are_strict(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        table_names = _user_table_names(connection)
        assert table_names

        non_strict = [
            row[1]
            for table_name in table_names
            for row in connection.execute(f"PRAGMA table_list('{table_name}')").fetchall()
            if row[1] == table_name and row[5] == 0
        ]

    assert non_strict == []


def test_tc_dbd_002_integer_column_rejects_text(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO work_status "
            "(code, name, is_billable, is_cost_target, sort_order, created_at, updated_at) "
            "VALUES ('TEST_DEV', '開発稼働', 1, 1, 'abc', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )


@pytest.mark.parametrize(
    "calendar_date",
    ["2026/04/01", "2026-4-1"],
)
def test_tc_dbd_003_date_format_is_rejected(db_path: Path, calendar_date: str) -> None:
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO business_calendar "
            "(calendar_date, is_business_day, source, created_at, updated_at) "
            "VALUES (?, 1, '自動取込', ?, ?)",
            (calendar_date, _TIMESTAMP, _TIMESTAMP),
        )


def test_tc_dbd_003_date_format_is_accepted(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO business_calendar "
            "(calendar_date, is_business_day, source, created_at, updated_at) "
            "VALUES ('2026-04-01', 1, '自動取込', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        row = connection.execute(
            "SELECT calendar_date FROM business_calendar WHERE calendar_date = '2026-04-01'"
        ).fetchone()
        assert row is not None


def test_tc_dbd_004_datetime_without_z_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO work_status "
            "(code, name, is_billable, is_cost_target, sort_order, created_at, updated_at) "
            "VALUES ('TEST_DEV', '開発稼働', 1, 1, 1, '2026-10-21 00:00:00', ?)",
            (_TIMESTAMP,),
        )


@pytest.mark.parametrize(
    "invalid_timestamp",
    [
        "2026-10-21T00:00:00Z",  # マイクロ秒が無い
        "2026-10-21T00:00:00.000000ABCZ",  # Zの前に余分な文字がある
    ],
)
def test_tc_dbd_004_datetime_not_matching_exact_format_is_rejected(
    db_path: Path, invalid_timestamp: str
) -> None:
    """Zの前に任意の文字列を許す緩いCHECKにならないことを確認する。"""
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO work_status "
            "(code, name, is_billable, is_cost_target, sort_order, created_at, updated_at) "
            "VALUES ('TEST_DEV', '開発稼働', 1, 1, 1, ?, ?)",
            (invalid_timestamp, _TIMESTAMP),
        )


def test_tc_dbd_004_datetime_with_z_is_accepted(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO work_status "
            "(code, name, is_billable, is_cost_target, sort_order, created_at, updated_at) "
            "VALUES ('TEST_DEV', '開発稼働', 1, 1, 1, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        row = connection.execute("SELECT code FROM work_status WHERE code = 'TEST_DEV'").fetchone()
        assert row is not None


def test_tc_dbd_005_boolean_column_rejects_other_than_zero_or_one(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO work_status "
            "(code, name, is_billable, is_cost_target, sort_order, created_at, updated_at) "
            "VALUES ('TEST_DEV', '開発稼働', 2, 1, 1, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )


def test_tc_dbd_006_enum_column_rejects_undefined_value(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO employee "
            "(email, name, enrollment_status, created_at, updated_at) "
            "VALUES ('taro@example.com', '山田太郎', '休職中', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )


def test_tc_dbd_006_bonus_rate_rejects_undefined_eval_rank(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO bonus_rate (eval_half, eval_rank, rate_bp, created_at, updated_at) "
            "VALUES ('2026-H1', 'E', 12500, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )


def test_tc_dbd_008_tables_have_created_at_and_updated_at_not_null(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        table_names = _user_table_names(connection)
        target_tables = [name for name in table_names if name not in _TABLES_WITHOUT_COMMON_COLUMNS]
        assert target_tables

        for table_name in target_tables:
            columns = {
                row[1]: row[3]  # 列名: notnull
                for row in connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
            }
            assert columns.get("created_at") == 1, table_name
            assert columns.get("updated_at") == 1, table_name
