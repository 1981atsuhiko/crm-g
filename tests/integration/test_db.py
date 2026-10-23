"""DB接続・トランザクション補助の試験（ARC-005）。"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crm_g.infrastructure.datetime_utils import from_iso8601, to_iso8601
from crm_g.infrastructure.db import UnsupportedSqliteVersionError, open_connection, transaction


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE parent (id INTEGER PRIMARY KEY);
        CREATE TABLE child (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER NOT NULL REFERENCES parent(id)
        );
        """
    )


def test_tc_arc_004_foreign_keys_are_enforced(tmp_path: Path) -> None:
    """新しい接続を作り、存在しない親を指す行を挿入する（TC-ARC-004）。"""
    db_path = tmp_path / "test.db"

    with open_connection(db_path) as connection:
        _create_schema(connection)

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")


def test_tc_dbd_018_foreign_keys_are_enforced_on_a_fresh_connection(tmp_path: Path) -> None:
    """新しい接続で、存在しない社員（親）を指す行を挿入する（TC-DBD-018）。

    TC-ARC-004 と同じ接続の設定（外部キー制約）で拒否されることを確認する。
    """
    db_path = tmp_path / "test.db"

    with open_connection(db_path) as connection:
        _create_schema(connection)

    with open_connection(db_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")


def test_open_connection_closes_connection_on_exit(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    with open_connection(db_path) as connection:
        pass

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_tc_arc_007_startup_aborts_on_unsupported_sqlite_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SQLiteの版を3.36.0（3.37.0未満）に差し替えて起動する（TC-ARC-007）。"""
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 36, 0))

    with (
        pytest.raises(UnsupportedSqliteVersionError, match="3.37.0"),
        open_connection(tmp_path / "test.db"),
    ):
        pass


def test_tc_arc_006_datetime_is_stored_in_iso8601_utc(tmp_path: Path) -> None:
    """時刻を2026-10-21T00:00:00Zに固定して、行を保存する（TC-ARC-006）。"""
    db_path = tmp_path / "test.db"
    fixed_time = datetime(2026, 10, 21, tzinfo=UTC)

    with open_connection(db_path) as connection:
        connection.execute("CREATE TABLE record (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO record (id, created_at) VALUES (1, ?)", (to_iso8601(fixed_time),)
        )
        connection.commit()

        row = connection.execute("SELECT created_at FROM record WHERE id = 1").fetchone()
        assert row[0] == "2026-10-21T00:00:00.000000Z"
        assert from_iso8601(row[0]) == fixed_time


def test_transaction_commits_on_success(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    with open_connection(db_path) as connection:
        _create_schema(connection)
        with transaction(connection):
            connection.execute("INSERT INTO parent (id) VALUES (1)")

    with open_connection(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM parent").fetchone()
        assert row[0] == 1


def test_tc_arc_005_transaction_rolls_back_on_failure(tmp_path: Path) -> None:
    """複数のテーブルを更新するユースケースの途中で、例外を起こす（TC-ARC-005）。

    すべてロールバックされ、同じ入力で再実行すると成功することを確認する。
    """
    db_path = tmp_path / "test.db"

    with open_connection(db_path) as connection:
        _create_schema(connection)
        with pytest.raises(ValueError, match="失敗させる"), transaction(connection):
            connection.execute("INSERT INTO parent (id) VALUES (1)")
            raise ValueError("試験のために失敗させる")

    with open_connection(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM parent").fetchone()
        assert row[0] == 0

        with transaction(connection):
            connection.execute("INSERT INTO parent (id) VALUES (1)")

        row = connection.execute("SELECT COUNT(*) FROM parent").fetchone()
        assert row[0] == 1
