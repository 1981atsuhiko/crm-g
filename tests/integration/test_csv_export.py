"""CSV 出力の共通処理の試験（`UID-090`、`UID-091`、`TC-SCN-018`）。"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from crm_g.application.csv_export import (
    build_csv,
    export_file_name,
    neutralize_formula,
    record_export,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("=SUM(A1)", "'=SUM(A1)"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@cmd", "'@cmd"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        ("\nx", "'\nx"),
        ("＝ａ", "'＝ａ"),
        ("＋ａ", "'＋ａ"),
        ("－ａ", "'－ａ"),
        ("＠ａ", "'＠ａ"),
        ("山田=太郎", "山田=太郎"),
        ("", ""),
    ],
)
def test_tc_scn_018_neutralize_formula(text: str, expected: str) -> None:
    assert neutralize_formula(text) == expected


def test_export_file_name() -> None:
    assert export_file_name("人事連携", "2026-11") == "人事連携_2026-11.csv"


def test_build_csv_format() -> None:
    content = build_csv(
        ["氏名", "金額", "備考"],
        [
            ["=山田", -5, None],
            ["鈴木,花子", 1000, 'a"b\nc'],
        ],
        text_columns=(0, 2),
    )

    # BOM 付きの UTF-8、改行は CRLF。値に `,`・`"`・改行を含むときだけ引用符で囲む。
    assert content.startswith(b"\xef\xbb\xbf")
    assert content.decode("utf-8-sig") == (
        '氏名,金額,備考\r\n\'=山田,-5,\r\n"鈴木,花子",1000,"a""b\nc"\r\n'
    )


def test_build_csv_neutralizes_only_text_columns_and_headers() -> None:
    content = build_csv(["a", "=b"], [["-1", "-1"]], text_columns=(1,))

    assert content.decode("utf-8-sig") == "a,'=b\r\n-1,'-1\r\n"


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        yield connection


def test_record_export_logs_each_table(connection: sqlite3.Connection) -> None:
    record_export(connection, ("monthly_payroll", "monthly_payroll_item"), "人事連携 2026-11")

    assert connection.execute(
        "SELECT operation, target_table, detail FROM operation_log ORDER BY id"
    ).fetchall() == [
        ("出力", "monthly_payroll", "人事連携 2026-11"),
        ("出力", "monthly_payroll_item", "人事連携 2026-11"),
    ]
