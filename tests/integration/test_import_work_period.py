"""`IMP-03` 稼働期間の取込の試験（移行設計書 `MIG-042`、`MGD-004`）。

テスト計画書の `TC-MIG-026`〜`TC-MIG-029` を含む。CSV は試験の中でバイト列として組み立てる。
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
_KIND = ImportKind.WORK_PERIOD
_HEADER = "氏名,開始日,終了日,稼働状態,顧客名,案件名,区分,稼働率,備考"
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


def _run(connection: sqlite3.Connection, db_path: Path, content: bytes) -> ImportReport:
    """取込の実行を、試験用のバックアップの作成先で呼ぶ。"""
    return run_import(
        connection, _KIND, content, _TODAY, db_path=db_path, backup_dir=db_path.parent / "backups"
    )


def _check(connection: sqlite3.Connection, *lines: str) -> list[tuple[int | None, str | None, str]]:
    """確認の実行をして、エラーを行番号・列名・コードの組にする。"""
    report = check_import(connection, _KIND, _csv(*lines), _TODAY)
    return [(issue.line_number, issue.column, issue.code.value) for issue in report.issues]


def _periods(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """稼働期間を、社員・開始日の順に返す。"""
    rows: list[tuple[object, ...]] = connection.execute(
        "SELECT employee_id, start_date, end_date, work_status_code, assignment_id, "
        "billing_ratio_bp, note FROM work_period ORDER BY employee_id, start_date"
    ).fetchall()
    return rows


def _assignments(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """参画を、ID の順に返す。"""
    rows: list[tuple[object, ...]] = connection.execute(
        "SELECT id, employee_id, customer_name, project_name, category, start_date, end_date "
        "FROM assignment ORDER BY id"
    ).fetchall()
    return rows


def _insert_assignment(
    connection: sqlite3.Connection,
    customer_name: str,
    project_name: str | None,
    start_date: str,
    end_date: str | None,
) -> int:
    """DB に、山田 太郎の既存の参画（SES）を1件入れる。"""
    cursor = connection.execute(
        "INSERT INTO assignment (employee_id, customer_name, project_name, category, start_date, "
        "end_date, created_at, updated_at) VALUES (?, ?, ?, 'SES', ?, ?, ?, ?)",
        (_TARO, customer_name, project_name, start_date, end_date, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _insert_period(connection: sqlite3.Connection, start_date: str, end_date: str | None) -> None:
    """DB に、山田 太郎の既存の稼働期間（待機）を1件入れる。"""
    connection.execute(
        "INSERT INTO work_period (employee_id, start_date, end_date, work_status_code, "
        "created_at, updated_at) VALUES (?, ?, ?, 'WAIT_CUT', ?, ?)",
        (_TARO, start_date, end_date, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()


def test_tc_mig_027_run_import_inserts_periods_and_assignments(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """`TC-MIG-027`：旧名称（`待機`・`現場稼働`）は `WAIT_CUT`・`DEV` として取り込まれる。

    売上計上の行は、参画を作って紐づける。稼働率は丸めずに万分率にする。空は 100 %。
    """
    report = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2025/04/01,2025/05/31,待機,,,,,",
            "山田 太郎,2025/06/01,,現場稼働,サンプル商事, 基幹システム開発 ,SES,33.33%,6/1入場",
            "鈴木 花子,2026/04/01,2026/09/30,教育稼働,教育センター,,教育原価,0.5,",
            "鈴木 花子,2026/10/01,,社内研修（減額なし）,,,,,",
        ),
    )

    assert report.executed
    assert (report.row_count, report.matched_row_count, report.imported_count) == (4, 4, 4)
    assert _assignments(connection) == [
        (1, _TARO, "サンプル商事", "基幹システム開発", "SES", "2025-06-01", None),
        (2, _HANAKO, "教育センター", None, "教育原価", "2026-04-01", "2026-09-30"),
    ]
    assert _periods(connection) == [
        (_TARO, "2025-04-01", "2025-05-31", "WAIT_CUT", None, 10000, None),
        (_TARO, "2025-06-01", None, "DEV", 1, 3333, "6/1入場"),
        (_HANAKO, "2026-04-01", "2026-09-30", "EDU", 2, 5000, None),
        (_HANAKO, "2026-10-01", None, "TRN_FULL", None, 10000, None),
    ]
    assert report.recalculated_month_count == 19
    assert connection.execute("SELECT target_table FROM operation_log").fetchall() == [
        ("work_period",)
    ]


def test_tc_mig_029_same_assignment_across_two_imports(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """`TC-MIG-029`：同じ顧客・案件を2回に分けて取り込むと、参画が1件にまとまり、期間が広がる。

    顧客名・案件名は正規化名で比べる。CSV 内の同じキーの行も、1件にまとめる。
    """
    first = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2025/06/01,2025/09/30,開発稼働,サンプル商事,基幹開発,SES,,",
            "山田 太郎,2025/10/01,2026/03/31,開発稼働,サンプル商事,基幹開発,SES,,",
        ),
    )
    second = _run(
        connection,
        db_path,
        _csv("山田 太郎,2026/04/01,2026/09/30,現場稼働,サンプル 商事,基幹開発,SES,,"),
    )

    assert first.executed
    assert second.executed
    assert _assignments(connection) == [
        (1, _TARO, "サンプル商事", "基幹開発", "SES", "2025-06-01", "2026-09-30"),
    ]
    assert [row[4] for row in _periods(connection)] == [1, 1, 1]


def test_existing_assignment_is_widened_both_ways(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """DB の参画の開始日は小さいほう、終了日はどちらかが空なら空（`MGD-004`）。"""
    assignment_id = _insert_assignment(connection, "サンプル商事", None, "2025-07-01", "2025-08-31")

    report = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2025/06/01,2025/06/30,開発稼働,サンプル商事,,SES,,",
            "山田 太郎,2025/09/01,,開発稼働,サンプル商事,,SES,,",
        ),
    )

    assert report.executed
    assert _assignments(connection) == [
        (assignment_id, _TARO, "サンプル商事", None, "SES", "2025-06-01", None),
    ]


def test_existing_assignment_covering_the_period_is_reused_unchanged(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """DB の参画の期間が、CSV の期間を含むときは、参画を変えずに紐づける。"""
    assignment_id = _insert_assignment(connection, "サンプル商事", "基幹", "2025-04-01", None)
    before = connection.execute("SELECT updated_at FROM assignment").fetchone()

    report = _run(
        connection,
        db_path,
        _csv("山田 太郎,2025/06/01,2025/06/30,開発稼働,サンプル商事,基幹,SES,,"),
    )

    assert report.executed
    assert connection.execute("SELECT updated_at FROM assignment").fetchone() == before
    assert [row[4] for row in _periods(connection)] == [assignment_id]


def test_different_category_or_project_makes_another_assignment(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """区分・案件名が違えば、別の参画にする。"""
    report = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2025/04/01,2025/04/30,開発稼働,サンプル商事,,SES,,",
            "山田 太郎,2025/05/01,2025/05/31,開発稼働,サンプル商事,,受託,,",
            "山田 太郎,2025/06/01,2025/06/30,開発稼働,サンプル商事,保守,受託,,",
        ),
    )

    assert report.executed
    assert [row[3:5] for row in _assignments(connection)] == [
        (None, "SES"),
        (None, "受託"),
        ("保守", "受託"),
    ]


def test_two_existing_assignments_with_the_same_key_is_e015(
    connection: sqlite3.Connection,
) -> None:
    """DB に同じキーの参画が複数あるときは、1件に決まらないため `E015`。"""
    _insert_assignment(connection, "サンプル商事", None, "2025-04-01", "2025-04-30")
    _insert_assignment(connection, "サンプル 商事", None, "2025-05-01", "2025-05-31")

    assert _check(connection, "山田 太郎,2025/06/01,,開発稼働,サンプル商事,,SES,,") == [
        (2, "顧客名", "E015")
    ]


def test_tc_mig_026_status_and_assignment_columns_combination(
    connection: sqlite3.Connection,
) -> None:
    """`TC-MIG-026`：稼働状態と、顧客名・案件名・区分の組み合わせの誤りは `E016`。

    売上計上の稼働状態に顧客名・区分がない、売上計上でない稼働状態に値がある、の両方を確かめる。
    """
    assert _check(
        connection,
        "山田 太郎,2025/04/01,2025/04/30,開発稼働,,,,,",
        "山田 太郎,2025/05/01,2025/05/31,開発稼働,サンプル商事,,,,",
        "山田 太郎,2025/06/01,2025/06/30,待機,サンプル商事,基幹,SES,,",
        "山田 太郎,2025/07/01,2025/07/31,開発稼働,,,SES,,",
    ) == [
        (2, "顧客名", "E016"),
        (2, "区分", "E016"),
        (3, "区分", "E016"),
        (4, "顧客名", "E016"),
        (4, "案件名", "E016"),
        (4, "区分", "E016"),
        (5, "顧客名", "E016"),
    ]


def test_row_validation_errors(connection: sqlite3.Connection) -> None:
    """行ごとの検証のエラー（必須・形式・選択肢・範囲・日付の前後）を、すべて集める。"""
    assert _check(
        connection,
        "山田 太郎,,2025/04/30,稼働,,,,,",
        "山田 太郎,2025/05/31,2025/05/01,開発稼働,サンプル商事,,派遣,70,",
        "山田 太郎,2025/06/01,2025/06/31,不明,,,SES,abc,",
        "山田 太郎,2025/07/01,,待機,,,,70.001%,",
    ) == [
        (2, "開始日", "E006"),
        (2, "稼働状態", "E008"),
        (3, "終了日", "E016"),
        (3, "区分", "E008"),
        (3, "稼働率", "E009"),
        (4, "終了日", "E007"),
        (4, "稼働状態", "E008"),
        (4, "稼働率", "E007"),
        (5, "稼働率", "E009"),
    ]


def test_tc_mig_028_overlapping_periods_in_file_are_e014(connection: sqlite3.Connection) -> None:
    """`TC-MIG-028`：同じ社員の期間が CSV 内で重なると `E014`。同じ開始日は `E012`。

    終了日が空（継続中）の行は、最後の期間でなければ、後の期間と重なる。他の社員とは比べない。
    """
    assert _check(
        connection,
        "山田 太郎,2025/04/01,2025/05/31,待機,,,,,",
        "山田 太郎,2025/05/31,2025/06/30,待機,,,,,",
        "山田 太郎,2025-4-1,2025/04/10,待機,,,,,",
        "山田 太郎,2025/07/01,,待機,,,,,",
        "山田 太郎,2025/09/01,2025/09/30,待機,,,,,",
        "鈴木 花子,2025/04/01,2025/05/31,待機,,,,,",
    ) == [
        (3, "開始日", "E014"),
        (4, "開始日", "E012"),
        (6, "開始日", "E014"),
    ]


def test_overlap_with_existing_period_in_db_is_e014(connection: sqlite3.Connection) -> None:
    """DB の既存の稼働期間と重なる行も `E014`（取込処理は、終了日を補わない）。"""
    _insert_period(connection, "2025-04-01", None)

    assert _check(
        connection,
        "山田 太郎,2025/03/01,2025/03/31,待機,,,,,",
        "山田 太郎,2026/04/01,,待機,,,,,",
        "鈴木 花子,2026/04/01,,待機,,,,,",
    ) == [(3, "開始日", "E014")]


def test_unmatched_employee_is_reported_and_nothing_is_imported(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """名寄せで一致しない行があると、何も取り込まない（参画も作らない）。"""
    report = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2025/04/01,,開発稼働,サンプル商事,,SES,,",
            "架空 存在,2025/04/01,,開発稼働,サンプル商事,,SES,,",
        ),
    )

    assert not report.executed
    assert [(issue.line_number, issue.code.value) for issue in report.issues] == [(3, "E010")]
    assert _assignments(connection) == []
    assert _periods(connection) == []


def test_status_matches_renamed_master_values(connection: sqlite3.Connection) -> None:
    """稼働状態は、マスタの現在の名称・旧名称と照合する（NFKC 正規化で括弧の全角半角を吸収）。"""
    connection.execute(
        "UPDATE work_status SET name = '開発', legacy_name = '旧開発' WHERE code = 'DEV'"
    )
    connection.commit()

    assert _check(
        connection,
        "山田 太郎,2025/04/01,2025/04/30,開発,顧客,,SES,,",
        "山田 太郎,2025/05/01,2025/05/31,旧開発,顧客,,SES,,",
        "山田 太郎,2025/06/01,2025/06/30,開発稼働,顧客,,SES,,",
        "山田 太郎,2025/07/01,2025/07/31,待機(減額あり),,,,,",
    ) == [(4, "稼働状態", "E008")]


def test_kind_label() -> None:
    """取込の種類の画面の名称。"""
    assert ImportKind("work-period").label == "稼働期間"
