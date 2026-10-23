"""DB接続・トランザクション補助の試験（ARC-005）。"""

import sqlite3
from pathlib import Path

import pytest

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


def test_open_connection_enables_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    with open_connection(db_path) as connection:
        _create_schema(connection)

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")


def test_open_connection_closes_connection_on_exit(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    with open_connection(db_path) as connection:
        pass

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_open_connection_rejects_unsupported_sqlite_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 36, 0))

    with (
        pytest.raises(UnsupportedSqliteVersionError, match="3.37.0"),
        open_connection(tmp_path / "test.db"),
    ):
        pass


def test_transaction_commits_on_success(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    with open_connection(db_path) as connection:
        _create_schema(connection)
        with transaction(connection):
            connection.execute("INSERT INTO parent (id) VALUES (1)")

    with open_connection(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM parent").fetchone()
        assert row[0] == 1


def test_transaction_rolls_back_on_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    with open_connection(db_path) as connection:
        _create_schema(connection)
        with pytest.raises(ValueError, match="失敗させる"), transaction(connection):
            connection.execute("INSERT INTO parent (id) VALUES (1)")
            raise ValueError("試験のために失敗させる")

    with open_connection(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM parent").fetchone()
        assert row[0] == 0
