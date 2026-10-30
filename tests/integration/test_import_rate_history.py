"""`IMP-04` 単価履歴の取込の試験（移行設計書 `MIG-043`）。

テスト計画書の `TC-MIG-030`、`TC-MIG-031` を含む。CSV は試験の中でバイト列として組み立てる。
値はすべて架空である。
"""

import shutil
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.data_import import ImportKind, ImportReport, check_import, run_import
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"
_TODAY = date(2026, 10, 27)
_KIND = ImportKind.RATE_HISTORY
_HEADER = "氏名,適用開始日,単価,顧客名,案件名,改定の根拠,備考"
_TARO = 1
_HANAKO = 2


def _csv(*lines: str, header: str = _HEADER) -> bytes:
    """見出しと行から、取込用 CSV（UTF-8、CRLF）のバイト列を作る。"""
    return "\r\n".join((header, *lines)).encode("utf-8") + b"\r\n"


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """マイグレーションを適用し、社員を入れた DB を、試験のモジュールで1回だけ作る（複写の元）。"""
    directory = tmp_path_factory.mktemp("migrated")
    path = directory / "template.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, directory / "migration-backups")
        connection.executemany(
            "INSERT INTO employee (id, email, name, enrollment_status, join_date, "
            "created_at, updated_at) VALUES (?, ?, ?, '在籍', '2020-04-01', ?, ?)",
            [
                (_TARO, "taro@example.com", "山田 太郎", _TIMESTAMP, _TIMESTAMP),
                (_HANAKO, "hanako@example.com", "鈴木 花子", _TIMESTAMP, _TIMESTAMP),
            ],
        )
        connection.commit()
    return path


@pytest.fixture
def db_path(tmp_path: Path, migrated_db: Path) -> Path:
    """複写した一時 DB。"""
    path = tmp_path / "test.db"
    shutil.copyfile(migrated_db, path)
    return path


@pytest.fixture
def connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """一時 DB への接続。"""
    with open_connection(db_path) as connection:
        yield connection


def _run(
    connection: sqlite3.Connection, db_path: Path, content: bytes, kind: ImportKind = _KIND
) -> ImportReport:
    """取込の実行を、試験用のバックアップの作成先で呼ぶ。"""
    return run_import(
        connection, kind, content, _TODAY, db_path=db_path, backup_dir=db_path.parent / "backups"
    )


def _check(connection: sqlite3.Connection, *lines: str) -> list[tuple[int | None, str | None, str]]:
    """確認の実行をして、エラーを行番号・列名・コードの組にする。"""
    report = check_import(connection, _KIND, _csv(*lines), _TODAY)
    return [(issue.line_number, issue.column, issue.code.value) for issue in report.issues]


def _rates(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """単価履歴を、参画・適用開始日の順に返す。"""
    rows: list[tuple[object, ...]] = connection.execute(
        "SELECT employee_id, assignment_id, effective_from, unit_price, revision_basis, note "
        "FROM rate_history ORDER BY assignment_id, effective_from"
    ).fetchall()
    return rows


def _insert_assignment(
    connection: sqlite3.Connection,
    employee_id: int,
    names: tuple[str, str | None],
    period: tuple[str, str | None],
) -> int:
    """DB に、既存の参画（SES）を1件入れる。

    `names` は（顧客名、案件名）、`period` は（開始日、終了日）。
    """
    customer_name, project_name = names
    start_date, end_date = period
    cursor = connection.execute(
        "INSERT INTO assignment (employee_id, customer_name, project_name, category, start_date, "
        "end_date, created_at, updated_at) VALUES (?, ?, ?, 'SES', ?, ?, ?, ?)",
        (employee_id, customer_name, project_name, start_date, end_date, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def test_run_import_inserts_rate_histories(connection: sqlite3.Connection, db_path: Path) -> None:
    """取り込むと、照合した参画に紐づけた単価履歴が入り、取り込んだ社員を再計算する。"""
    taro_assignment = _insert_assignment(
        connection, _TARO, ("サンプル商事", "基幹システム開発"), ("2025-06-01", None)
    )
    hanako_assignment = _insert_assignment(
        connection, _HANAKO, ("テスト工業", None), ("2026-04-01", "2026-09-30")
    )

    report = _run(
        connection,
        db_path,
        _csv(
            '山田 太郎,2025/06/01,"700,000",サンプル 商事,基幹システム開発,入場時の契約, 備考 ',
            "山田 太郎,2026/04/01,750000,サンプル商事,基幹システム開発,単価改定,",
            "鈴木 花子,2026/9/30,0,テスト工業,,,",
        ),
    )

    assert report.executed
    assert (report.row_count, report.matched_row_count, report.imported_count) == (3, 3, 3)
    assert _rates(connection) == [
        (_TARO, taro_assignment, "2025-06-01", 700000, "入場時の契約", "備考"),
        (_TARO, taro_assignment, "2026-04-01", 750000, "単価改定", None),
        (_HANAKO, hanako_assignment, "2026-09-30", 0, None, None),
    ]
    assert report.recalculated_month_count == 17
    assert connection.execute("SELECT target_table FROM operation_log").fetchall() == [
        ("rate_history",)
    ]


def test_work_period_then_rate_history(connection: sqlite3.Connection, db_path: Path) -> None:
    """`IMP-03` で取り込んだ参画に、`IMP-04` の単価を紐づけられる（`MIG-051`）。"""
    work = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2025/06/01,,現場稼働,サンプル商事,基幹,SES,,",
            header="氏名,開始日,終了日,稼働状態,顧客名,案件名,区分,稼働率,備考",
        ),
        ImportKind.WORK_PERIOD,
    )
    rate = _run(connection, db_path, _csv("山田 太郎,2025/06/01,700000,サンプル商事,基幹,,"))

    assert work.executed
    assert rate.executed
    assert [row[1:4] for row in _rates(connection)] == [(1, "2025-06-01", 700000)]


def test_tc_mig_030_ambiguous_or_missing_assignment_is_e015(
    connection: sqlite3.Connection,
) -> None:
    """`TC-MIG-030`：顧客名・案件名が一致する参画がないと `E015`。案件名で1件に決まれば紐づく。

    案件名が空の行は、案件名が空の参画とだけ一致する（案件名のある参画には紐づけない）。
    他の社員の参画は探さない。
    """
    _insert_assignment(connection, _TARO, ("サンプル商事", "基幹"), ("2025-04-01", None))
    _insert_assignment(connection, _TARO, ("サンプル商事", "保守"), ("2025-04-01", None))
    _insert_assignment(connection, _HANAKO, ("テスト工業", None), ("2025-04-01", None))

    assert _check(
        connection,
        "山田 太郎,2025/06/01,1,サンプル商事,,,",
        "山田 太郎,2025/06/01,1,サンプル商事,保守,,",
        "山田 太郎,2025/06/01,1,サンプル商事,運用,,",
        "山田 太郎,2025/06/01,1,テスト工業,,,",
    ) == [
        (2, "顧客名", "E015"),
        (4, "顧客名", "E015"),
        (5, "顧客名", "E015"),
    ]


def test_work_period_with_and_without_project_name(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """`IMP-03` で作った案件名なし・ありの同じ顧客の参画に、案件名の有無で紐づく（`MGD-004`）。"""
    work = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2025/04/01,2025/05/31,現場稼働,サンプル商事,,SES,,",
            "山田 太郎,2025/06/01,,現場稼働,サンプル商事,基幹,SES,,",
            header="氏名,開始日,終了日,稼働状態,顧客名,案件名,区分,稼働率,備考",
        ),
        ImportKind.WORK_PERIOD,
    )
    assignments = dict(
        connection.execute(
            "SELECT COALESCE(project_name, ''), id FROM assignment WHERE employee_id = ?",
            (_TARO,),
        ).fetchall()
    )
    rate = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2025/04/01,600000,サンプル商事,,,",
            "山田 太郎,2025/06/01,700000,サンプル商事,基幹,,",
        ),
    )

    assert work.executed
    assert rate.executed
    assert [row[1:4] for row in _rates(connection)] == [
        (assignments[""], "2025-04-01", 600000),
        (assignments["基幹"], "2025-06-01", 700000),
    ]


def test_same_names_are_chosen_by_effective_from(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """同じ顧客名・案件名で期間が別の参画は、適用開始日を含む期間の参画に紐づく。"""
    first = _insert_assignment(
        connection, _TARO, ("サンプル商事", "保守"), ("2025-04-01", "2025-09-30")
    )
    second = _insert_assignment(connection, _TARO, ("サンプル商事", "保守"), ("2026-04-01", None))

    report = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2025/09/30,600000,サンプル商事,保守,,",
            "山田 太郎,2026/04/01,700000,サンプル商事,保守,,",
        ),
    )

    assert report.executed
    assert [row[1:4] for row in _rates(connection)] == [
        (first, "2025-09-30", 600000),
        (second, "2026-04-01", 700000),
    ]


def test_same_names_outside_every_period_is_e016(connection: sqlite3.Connection) -> None:
    """同じ顧客名・案件名の参画が複数あり、どの期間にも適用開始日が入らないときは `E016`。"""
    _insert_assignment(connection, _TARO, ("サンプル商事", "保守"), ("2025-04-01", "2025-09-30"))
    _insert_assignment(connection, _TARO, ("サンプル商事", "保守"), ("2026-04-01", None))

    assert _check(
        connection,
        "山田 太郎,2025/03/31,1,サンプル商事,保守,,",
        "山田 太郎,2025/12/01,1,サンプル商事,保守,,",
    ) == [
        (2, "適用開始日", "E016"),
        (3, "適用開始日", "E016"),
    ]


def test_overlapping_same_names_is_e015(connection: sqlite3.Connection) -> None:
    """同じ顧客名・案件名の参画の期間が重なり、1件に決まらないときは `E015`。

    重ならない日は、その日を含む参画に紐づく。適用開始日が正しくないときも `E015` を出す。
    """
    _insert_assignment(connection, _TARO, ("テスト工業", None), ("2025-04-01", None))
    _insert_assignment(connection, _TARO, ("テスト工業", None), ("2025-06-01", "2025-12-31"))

    assert _check(
        connection,
        "山田 太郎,2025/07/01,1,テスト工業,,,",
        "山田 太郎,2025/05/01,1,テスト工業,,,",
        "山田 太郎,2025/02/30,1,テスト工業,,,",
    ) == [
        (2, "顧客名", "E015"),
        (4, "適用開始日", "E007"),
        (4, "顧客名", "E015"),
    ]


def test_tc_mig_031_empty_customer_name_is_e006(connection: sqlite3.Connection) -> None:
    """`TC-MIG-031`：顧客名が空の行は `E006`。顧客名なしで案件名があると `E016` も出す。"""
    assert _check(
        connection,
        "山田 太郎,2025/06/01,1,,,,",
        "山田 太郎,2025/07/01,1,,基幹,,",
    ) == [
        (2, "顧客名", "E006"),
        (3, "顧客名", "E006"),
        (3, "案件名", "E016"),
    ]


def test_effective_from_outside_assignment_period_is_e016(
    connection: sqlite3.Connection,
) -> None:
    """適用開始日が、参画の開始日〜終了日の外にあるときは `E016`。境界の日は取り込める。"""
    _insert_assignment(connection, _TARO, ("サンプル商事", None), ("2025-06-01", "2025-09-30"))

    assert _check(
        connection,
        "山田 太郎,2025/05/31,1,サンプル商事,,,",
        "山田 太郎,2025/06/01,1,サンプル商事,,,",
        "山田 太郎,2025/09/30,1,サンプル商事,,,",
        "山田 太郎,2025/10/01,1,サンプル商事,,,",
    ) == [
        (2, "適用開始日", "E016"),
        (5, "適用開始日", "E016"),
    ]


def test_duplicate_assignment_and_date_is_e012_or_e013(connection: sqlite3.Connection) -> None:
    """（参画、適用開始日）が CSV 内で重複すると `E012`、DB に既にあると `E013`。"""
    assignment_id = _insert_assignment(
        connection, _TARO, ("サンプル商事", None), ("2025-04-01", None)
    )
    connection.execute(
        "INSERT INTO rate_history (employee_id, assignment_id, effective_from, unit_price, "
        "created_at, updated_at) VALUES (?, ?, '2025-04-01', 600000, ?, ?)",
        (_TARO, assignment_id, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()

    assert _check(
        connection,
        "山田 太郎,2025/04/01,1,サンプル商事,,,",
        "山田 太郎,2025/06/01,1,サンプル商事,,,",
        "山田太郎,2025-6-1,2,サンプル商事,,,",
    ) == [
        (2, "適用開始日", "E013"),
        (4, "適用開始日", "E012"),
    ]


def test_row_validation_errors(connection: sqlite3.Connection) -> None:
    """行ごとの検証のエラー（必須・形式・範囲）と、名寄せのエラーを、すべて集める。"""
    assert _check(
        connection,
        "山田 太郎,,,サンプル商事,,,",
        "山田 太郎,2025/02/30,1.5,サンプル商事,,,",
        "山田 太郎,2025/06/01,-1,サンプル商事,,,",
        "架空 存在,2025/06/01,1,サンプル商事,,,",
    ) == [
        (2, "適用開始日", "E006"),
        (2, "単価", "E006"),
        (2, "顧客名", "E015"),
        (3, "適用開始日", "E007"),
        (3, "単価", "E007"),
        (3, "顧客名", "E015"),
        (4, "単価", "E009"),
        (4, "顧客名", "E015"),
        (5, "氏名", "E010"),
    ]


def test_kind_label() -> None:
    """取込の種類の画面の名称。"""
    assert ImportKind("rate-history").label == "単価履歴"
