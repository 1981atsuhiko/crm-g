"""`IMP-02` 給与履歴の取込と、名寄せ・取込後の再計算の試験（移行設計書 `MIG-041`、`MIG-054`）。

テスト計画書の `TC-MIG-014`〜`TC-MIG-017`、`TC-MIG-022`〜`TC-MIG-025`、`TC-MIG-043` を含む。
CSV は試験の中でバイト列として組み立てる。値はすべて架空である。
"""

import logging
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.data_import import ImportKind, ImportReport, check_import, run_import
from crm_g.application.import_salary_history import pay_item_columns, salary_history_layout
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"
_TODAY = date(2026, 10, 27)
_KIND = ImportKind.SALARY_HISTORY
_HEADER = (
    "氏名,適用開始日,給与体系,雇用形態,等級,年俸年収,改定区分,備考,"
    "基本給,勤務手当（職務給）,住宅手当"
)
_EMAIL_HEADER = "氏名,メールアドレス,適用開始日,給与体系,雇用形態,改定区分,基本給"

# 社員の ID（_insert_employees で入れる）。
_TARO = 1
_HANAKO = 2
_ICHIRO_A = 3
_ICHIRO_B = 4
_JIRO = 5


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
        _insert_employees(connection)
    return path


def _insert_employees(connection: sqlite3.Connection) -> None:
    """名寄せの対象の社員を入れる（同姓同名の2人と、`高` の字の社員を含む）。"""
    connection.executemany(
        "INSERT INTO employee (id, email, name, enrollment_status, join_date, "
        "created_at, updated_at) VALUES (?, ?, ?, '在籍', '2020-04-01', ?, ?)",
        [
            (_TARO, "taro@example.com", "山田 太郎", _TIMESTAMP, _TIMESTAMP),
            (_HANAKO, "hanako@example.com", "鈴木 花子", _TIMESTAMP, _TIMESTAMP),
            (_ICHIRO_A, "ichiro.a@example.com", "佐藤 一郎", _TIMESTAMP, _TIMESTAMP),
            (_ICHIRO_B, "ichiro.b@example.com", "佐藤 一郎", _TIMESTAMP, _TIMESTAMP),
            (_JIRO, "jiro@example.com", "高橋 次郎", _TIMESTAMP, _TIMESTAMP),
        ],
    )
    connection.commit()


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


@pytest.fixture
def captured_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """crm_g のロガーの出力を、伝播の設定によらず捕まえる。"""
    logger = logging.getLogger("crm_g")
    logger.addHandler(caplog.handler)
    original_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(original_level)


def _run(connection: sqlite3.Connection, db_path: Path, content: bytes) -> ImportReport:
    """取込の実行を、試験用のバックアップの作成先で呼ぶ。"""
    return run_import(
        connection, _KIND, content, _TODAY, db_path=db_path, backup_dir=db_path.parent / "backups"
    )


def _issues(report: ImportReport) -> list[tuple[int | None, str | None, str]]:
    """エラーを、行番号・列名・コードの組にする。"""
    return [(issue.line_number, issue.column, issue.code.value) for issue in report.issues]


def _histories(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """給与履歴を、社員・適用開始日の順に返す。"""
    rows: list[tuple[object, ...]] = connection.execute(
        "SELECT employee_id, effective_from, effective_to, pay_system, employment_type, grade, "
        "annual_salary, revision_type, note FROM salary_history "
        "ORDER BY employee_id, effective_from"
    ).fetchall()
    return rows


def _items(connection: sqlite3.Connection, employee_id: int) -> list[tuple[object, ...]]:
    """社員の内訳を、適用開始日・給与項目コードの順に返す。"""
    rows: list[tuple[object, ...]] = connection.execute(
        "SELECT h.effective_from, i.pay_item_code, i.amount FROM salary_history_item i "
        "JOIN salary_history h ON h.id = i.salary_history_id WHERE h.employee_id = ? "
        "ORDER BY h.effective_from, i.pay_item_code",
        (employee_id,),
    ).fetchall()
    return rows


def _insert_history(
    connection: sqlite3.Connection,
    employee_id: int,
    effective_from: str,
    effective_to: str | None = None,
) -> None:
    """DB に、既存の給与履歴（基本給だけ）を1件入れる。"""
    cursor = connection.execute(
        "INSERT INTO salary_history (employee_id, effective_from, effective_to, pay_system, "
        "employment_type, revision_type, created_at, updated_at) "
        "VALUES (?, ?, ?, '通常評価制度', '正社員', '入社', ?, ?)",
        (employee_id, effective_from, effective_to, _TIMESTAMP, _TIMESTAMP),
    )
    connection.execute(
        "INSERT INTO salary_history_item (salary_history_id, pay_item_code, amount, "
        "created_at, updated_at) VALUES (?, 'BASE_SALARY', 200000, ?, ?)",
        (cursor.lastrowid, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()


def test_run_import_inserts_histories_items_and_effective_to(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """取り込むと、履歴・内訳が入り、同じ社員の履歴の適用終了日が設定される（`MGD-002`）。"""
    content = _csv(
        "山田 太郎,2025/10/01,通常評価制度,正社員,P5-15,,定期（10月）, 昇給 ,260000,30000,",
        "山田\u3000太郎,2025/04/01,通常評価制度,正社員,,,入社,,250000,30000,0",
        '鈴木 花子,2026-4-1,年俸制,契約社員,,"6,000,000",制度移行,,500000,,',
    )

    report = _run(connection, db_path, content)

    assert report.executed
    assert report.issues == ()
    assert (report.row_count, report.matched_row_count, report.imported_count) == (3, 3, 3)
    assert report.unmatched == ()
    assert _histories(connection) == [
        (_TARO, "2025-04-01", "2025-09-30", "通常評価制度", "正社員", None, None, "入社", None),
        (
            _TARO,
            "2025-10-01",
            None,
            "通常評価制度",
            "正社員",
            "P5-15",
            None,
            "定期（10月）",
            "昇給",
        ),
        (_HANAKO, "2026-04-01", None, "年俸制", "契約社員", None, 6000000, "制度移行", None),
    ]
    # 空のセルは内訳の行を作らず、0 は金額 0 の行を作る。
    assert _items(connection, _TARO) == [
        ("2025-04-01", "BASE_SALARY", 250000),
        ("2025-04-01", "DUTY_ALLOWANCE", 30000),
        ("2025-04-01", "HOUSING", 0),
        ("2025-10-01", "BASE_SALARY", 260000),
        ("2025-10-01", "DUTY_ALLOWANCE", 30000),
    ]
    assert _items(connection, _HANAKO) == [("2026-04-01", "BASE_SALARY", 500000)]
    operation = connection.execute(
        "SELECT operation, target_table, detail FROM operation_log"
    ).fetchall()
    assert operation == [
        ("取込", "salary_history", "種類=salary-history 行数=3 再計算した月数=19 計算エラー=38")
    ]


def test_tc_mig_043_recalculation_errors_do_not_roll_back(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """`TC-MIG-043`：再計算で計算エラー（営業日カレンダーが未作成）が出ても、コミットされる。

    再計算の対象は、取り込んだ社員の、最も早い適用開始日の月から今月まで（`MIG-054`）。
    """
    report = _run(connection, db_path, _csv("鈴木 花子,2026/09/01,通常評価制度,正社員,,,入社,,1,,"))

    assert report.executed
    assert report.recalculated_month_count == 2
    assert report.calculation_error_count == 2
    assert report.recalculation is not None
    assert {error.employee_id for error in report.recalculation.errors} == {_HANAKO}
    connection.rollback()
    assert len(_histories(connection)) == 1


def test_tc_mig_024_second_import_closes_the_previous_history(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """`TC-MIG-024`：19期・20期の順に取り込むと、19期の履歴の適用終了日が前日になる。"""
    _run(connection, db_path, _csv("山田 太郎,2025/04/01,通常評価制度,正社員,,,入社,,250000,,"))
    report = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2026/10/01,通常評価制度,正社員,,,定期（10月）,,280000,,",
            "山田 太郎,2026/04/01,通常評価制度,正社員,P5-15,,制度移行,,270000,,",
        ),
    )

    assert report.executed
    assert [row[1:3] for row in _histories(connection)] == [
        ("2025-04-01", "2026-03-31"),
        ("2026-04-01", "2026-09-30"),
        ("2026-10-01", None),
    ]


def test_closed_latest_history_is_not_changed(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """最新の履歴の適用終了日が日付なら、その後の日付は取り込め、最新の履歴は変えない。"""
    _insert_history(connection, _TARO, "2025-04-01", "2025-09-30")

    report = _run(
        connection, db_path, _csv("山田 太郎,2025/11/01,通常評価制度,正社員,,,その他,,1,,")
    )

    assert report.executed
    assert [row[1:3] for row in _histories(connection)] == [
        ("2025-04-01", "2025-09-30"),
        ("2025-11-01", None),
    ]


def test_tc_mig_025_history_before_latest_is_e017(connection: sqlite3.Connection) -> None:
    """`TC-MIG-025`：既存の最新の履歴の適用開始日以前・適用終了日以前の行は `E017`。"""
    _insert_history(connection, _TARO, "2025-04-01", None)
    _insert_history(connection, _TARO, "2026-04-01", None)
    _insert_history(connection, _HANAKO, "2025-04-01", "2025-09-30")

    report = check_import(
        connection,
        _KIND,
        _csv(
            "山田 太郎,2026/03/01,通常評価制度,正社員,,,その他,,1,,",
            "山田 太郎,2026/04/01,通常評価制度,正社員,,,その他,,1,,",
            "山田 太郎,2026/04/02,通常評価制度,正社員,,,その他,,1,,",
            "鈴木 花子,2025/09/30,通常評価制度,正社員,,,その他,,1,,",
            "鈴木 花子,2025/10/01,通常評価制度,正社員,,,その他,,1,,",
        ),
        _TODAY,
    )

    assert _issues(report) == [
        (2, "適用開始日", "E017"),
        (3, "適用開始日", "E017"),
        (5, "適用開始日", "E017"),
    ]


def test_tc_mig_022_all_pay_items_empty_is_e006(connection: sqlite3.Connection) -> None:
    """`TC-MIG-022`：給与項目の列がすべて空の行・給与項目の列がない CSV は `E006`。"""
    empty = check_import(
        connection, _KIND, _csv("山田 太郎,2025/04/01,通常評価制度,正社員,,,入社,,,,"), _TODAY
    )
    no_item_columns = check_import(
        connection,
        _KIND,
        _csv(
            "山田 太郎,2025/04/01,通常評価制度,正社員,入社",
            header="氏名,適用開始日,給与体系,雇用形態,改定区分",
        ),
        _TODAY,
    )

    assert _issues(empty) == [(2, "給与項目の列", "E006")]
    assert _issues(no_item_columns) == [(2, "給与項目の列", "E006")]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # TC-MIG-023：年俸制でないのに年俸年収がある。
        ("山田 太郎,2025/04/01,通常評価制度,正社員,,1000,入社,,1,,", [(2, "年俸年収", "E016")]),
        # 年俸制なのに年俸年収がない。
        ("山田 太郎,2025/04/01,年俸制,正社員,,,入社,,1,,", [(2, "年俸年収", "E016")]),
        # 年俸年収の形式の誤りは、組み合わせの誤りにしない。
        ("山田 太郎,2025/04/01,年俸制,正社員,,1000.5,入社,,1,,", [(2, "年俸年収", "E007")]),
        ("山田 太郎,2025/04/01,年俸制,正社員,,-1,入社,,1,,", [(2, "年俸年収", "E009")]),
    ],
)
def test_tc_mig_023_annual_salary_combination(
    connection: sqlite3.Connection, line: str, expected: list[tuple[int, str, str]]
) -> None:
    """`TC-MIG-023`：給与体系と年俸年収の組み合わせの誤りは `E016`。"""
    assert _issues(check_import(connection, _KIND, _csv(line), _TODAY)) == expected


def test_row_validation_errors(connection: sqlite3.Connection) -> None:
    """行ごとの検証のエラー（必須・形式・選択肢・範囲）を、すべて集める。"""
    report = check_import(
        connection,
        _KIND,
        _csv(
            "山田 太郎,,通常評価制度,正社員,,,入社,,1,,",
            "山田 太郎,2025/13/01,月給制,アルバイト,,,昇給,,1.5,¥1,-1",
            "山田 太郎,2025/04/01,,,,,,,1,,",
        ),
        _TODAY,
    )

    assert _issues(report) == [
        (2, "適用開始日", "E006"),
        (3, "適用開始日", "E007"),
        (3, "給与体系", "E008"),
        (3, "雇用形態", "E008"),
        (3, "改定区分", "E008"),
        (3, "基本給", "E007"),
        (3, "勤務手当（職務給）", "E007"),
        (3, "住宅手当", "E009"),
        (4, "給与体系", "E006"),
        (4, "雇用形態", "E006"),
        (4, "改定区分", "E006"),
    ]


def test_same_employee_and_date_twice_is_e012(connection: sqlite3.Connection) -> None:
    """同じ社員・同じ適用開始日の行が CSV 内にあるときは、2回目以降の行を `E012`。"""
    report = check_import(
        connection,
        _KIND,
        _csv(
            "山田 太郎,2025/04/01,通常評価制度,正社員,,,入社,,1,,",
            "鈴木 花子,2025/04/01,通常評価制度,正社員,,,入社,,1,,",
            "山田太郎,2025-04-01,通常評価制度,正社員,,,その他,,2,,",
        ),
        _TODAY,
    )

    assert _issues(report) == [(4, "適用開始日", "E012")]


def test_pay_item_columns_use_master_names(connection: sqlite3.Connection, db_path: Path) -> None:
    """給与項目の列は、マスタの名称と正規化して照合する。マスタの有効期間は検査しない。"""
    connection.execute(
        "INSERT INTO pay_item (code, name, category, in_monthly_salary, cost_direct, sort_order, "
        "valid_from, created_at, updated_at) "
        "VALUES ('FUTURE', '将来手当', '固定支給', 1, 0, 99, '2030-04-01', ?, ?)",
        (_TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()
    header = "氏名,適用開始日,給与体系,雇用形態,改定区分,勤務手当(職務給),将来手当"

    report = _run(
        connection,
        db_path,
        _csv("山田 太郎,2025/04/01,通常評価制度,正社員,入社,1,2", header=header),
    )
    unknown = check_import(
        connection,
        _KIND,
        _csv(
            "山田 太郎,2025/04/01,通常評価制度,正社員,入社,1",
            header="氏名,適用開始日,給与体系,雇用形態,改定区分,未登録手当",
        ),
        _TODAY,
    )

    assert report.executed
    assert _items(connection, _TARO) == [
        ("2025-04-01", "DUTY_ALLOWANCE", 1),
        ("2025-04-01", "FUTURE", 2),
    ]
    assert _issues(unknown) == [(1, "未登録手当", "E004")]


def test_layout_includes_pay_item_names() -> None:
    """列の定義に、給与項目の名称の列が加わり、氏名・メールアドレスのどちらかが必要。"""
    layout = salary_history_layout(("基本給",))

    assert layout.defined[-1] == "基本給"
    assert layout.required_one_of == (("氏名", "メールアドレス"),)


def test_layout_rejects_pay_item_name_overlapping_fixed_column() -> None:
    """固定の列と重なる名称を、そのまま列の定義に加えると `ValueError`（名称は含めない）。"""
    with pytest.raises(ValueError, match="正規化すると同じになる列名") as raised:
        salary_history_layout(("備考",))

    assert "備考" not in str(raised.value)


def test_pay_item_columns_sorts_out_ambiguous_names() -> None:
    """固定の列・除外列・ほかの給与項目の名称と重なる名称は、1件に決まらない列とする。"""
    columns = pay_item_columns(
        [
            ("基本給", "BASE_SALARY"),
            (" 備考 ", "NOTE_ITEM"),
            ("支払額", "PAID"),
            ("通勤手当", "COMMUTE_A"),
            ("通勤手当\u200b", "COMMUTE_B"),
            ("住宅手当", "HOUSING"),
        ]
    )

    assert dict(columns.codes_by_name) == {"基本給": "BASE_SALARY", "住宅手当": "HOUSING"}
    assert columns.ambiguous == ("備考", "支払額", "通勤手当")
    assert columns.layout_names == ("基本給", "住宅手当", "通勤手当")
    # 振り分けた名称は、列の定義に加えても ValueError にならない。
    salary_history_layout(columns.layout_names)


def _insert_pay_item(connection: sqlite3.Connection, code: str, name: str) -> None:
    """給与項目マスタに、項目を1件入れる。"""
    connection.execute(
        "INSERT INTO pay_item (code, name, category, in_monthly_salary, cost_direct, sort_order, "
        "valid_from, created_at, updated_at) "
        "VALUES (?, ?, '固定支給', 1, 0, 99, '2020-04-01', ?, ?)",
        (code, name, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()


def test_ambiguous_pay_item_column_in_header_is_e015(connection: sqlite3.Connection) -> None:
    """見出しに、1件に決まらない給与項目の列があると、見出し行の `E015`（値は含めない）。

    固定の列（`備考`）・除外列（`支払額`）と重なる名称、互いに重なる名称（`通勤手当`）が対象。
    """
    _insert_pay_item(connection, "NOTE_ITEM", "備考")
    _insert_pay_item(connection, "PAID", "支払額")
    _insert_pay_item(connection, "COMMUTE_A", "通勤手当")
    _insert_pay_item(connection, "COMMUTE_B", "通勤手当 ")
    header = "氏名,適用開始日,給与体系,雇用形態,改定区分,備考,支払額,基本給,通勤手当"

    report = check_import(
        connection,
        _KIND,
        _csv("山田 太郎,2025/04/01,通常評価制度,正社員,入社,,123,1,456", header=header),
        _TODAY,
    )

    # 並びは、給与項目マスタの表示順（同じ表示順はコードの順）。
    assert _issues(report) == [(1, "通勤手当", "E015"), (1, "備考", "E015"), (1, "支払額", "E015")]
    for value in ("123", "456"):
        assert all(value not in str(issue) for issue in report.issues)


def test_ambiguous_pay_item_column_not_in_header_is_ignored(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """1件に決まらない給与項目の列が見出しになければ、ほかの列は取り込める。"""
    _insert_pay_item(connection, "COMMUTE_A", "通勤手当")
    _insert_pay_item(connection, "COMMUTE_B", "通勤手当")
    header = "氏名,適用開始日,給与体系,雇用形態,改定区分,基本給"

    report = _run(
        connection, db_path, _csv("山田 太郎,2025/04/01,通常評価制度,正社員,入社,1", header=header)
    )
    header_only = check_import(
        connection,
        _KIND,
        _csv(header="氏名,適用開始日,給与体系,雇用形態,改定区分,通勤手当"),
        _TODAY,
    )

    assert report.executed
    assert _items(connection, _TARO) == [("2025-04-01", "BASE_SALARY", 1)]
    # 行がなければ、取り込むものがないため検査しない。
    assert header_only.issues == ()


def test_tc_mig_014_variant_character_is_e010_and_listed(connection: sqlite3.Connection) -> None:
    """`TC-MIG-014`：異体字（`髙`）の氏名は `E010`。一致しなかった人の一覧に氏名が出る。"""
    report = check_import(
        connection, _KIND, _csv("髙橋 次郎,2025/04/01,通常評価制度,正社員,,,入社,,1,,"), _TODAY
    )

    assert _issues(report) == [(2, "氏名", "E010")]
    assert report.matched_row_count == 0
    assert [(person.name, person.line_numbers) for person in report.unmatched] == [
        ("髙橋 次郎", (2,))
    ]


def test_tc_mig_015_and_016_same_name_needs_email(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """`TC-MIG-015`：同姓同名の氏名だけの行は `E011`。`TC-MIG-016`：メールアドレスで紐づく。"""
    ambiguous = check_import(
        connection,
        _KIND,
        _csv("佐藤 一郎,,2025/04/01,通常評価制度,正社員,入社,1", header=_EMAIL_HEADER),
        _TODAY,
    )
    by_email = _run(
        connection,
        db_path,
        _csv(
            "佐藤 一郎,ICHIRO.B@example.com,2025/04/01,通常評価制度,正社員,入社,1",
            header=_EMAIL_HEADER,
        ),
    )

    assert _issues(ambiguous) == [(2, "氏名", "E011")]
    assert by_email.executed
    assert [row[0] for row in _histories(connection)] == [_ICHIRO_B]


def test_tc_mig_017_unmatched_row_imports_nothing_and_name_is_not_logged(
    connection: sqlite3.Connection, db_path: Path, captured_log: pytest.LogCaptureFixture
) -> None:
    """`TC-MIG-017`：一致しない行があると何も取り込まない。氏名・金額はログに出ない。"""
    report = _run(
        connection,
        db_path,
        _csv(
            "山田 太郎,2025/04/01,通常評価制度,正社員,,,入社,,987654,,",
            "架空 存在,2025/04/01,通常評価制度,正社員,,,入社,,1,,",
        ),
    )

    assert not report.executed
    assert _issues(report) == [(3, "氏名", "E010")]
    assert [person.name for person in report.unmatched] == ["架空 存在"]
    assert _histories(connection) == []
    log_text = captured_log.text
    assert "取り込みませんでした" in log_text
    for value in ("架空", "山田", "987654", "taro@example.com"):
        assert value not in log_text


def test_check_import_does_not_change_db(connection: sqlite3.Connection) -> None:
    """確認の実行は、DB を変えない（既存の最新の履歴も閉じない）。"""
    _insert_history(connection, _TARO, "2025-04-01", None)

    report = check_import(
        connection, _KIND, _csv("山田 太郎,2026/04/01,通常評価制度,正社員,,,制度移行,,1,,"), _TODAY
    )

    assert report.issues == ()
    assert [row[1:3] for row in _histories(connection)] == [("2025-04-01", None)]
    assert connection.execute("SELECT COUNT(*) FROM operation_log").fetchone() == (0,)


def test_kind_label() -> None:
    """取込の種類の画面の名称。"""
    assert ImportKind("salary-history").label == "給与履歴"


def test_header_only_csv_imports_nothing_and_does_not_recalculate(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """見出しだけの CSV は、0 件を取り込み、再計算しない。"""
    report = _run(connection, db_path, _csv())

    assert report.executed
    assert (report.row_count, report.imported_count) == (0, 0)
    assert report.recalculation is None
