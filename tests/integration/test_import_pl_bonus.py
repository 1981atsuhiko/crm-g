"""`IMP-05` 月次売上原価・`IMP-06` 賞与の取込の試験（移行設計書 `MIG-044`、`MIG-045`、`MIG-054`）。

テスト計画書の `TC-MIG-032`〜`TC-MIG-038` を含む。CSV は試験の中でバイト列として組み立てる。
値はすべて架空である。
"""

import shutil
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.data_import import ImportKind, ImportReport, check_import, run_import
from crm_g.application.import_bonus import BonusRecord, employees_by_pay_month
from crm_g.application.import_monthly_pl import (
    MonthlyPlConflictError,
    MonthlyPlRecord,
    insert_monthly_pl,
)
from crm_g.application.monthly_close import close_month, reopen_month
from crm_g.application.recalculation import (
    CalculationErrorReason,
    is_month_closed,
    recalculate_month,
    recalculate_range,
)
from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.application.work_period import WorkPeriodInput, create_work_period
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-01T00:00:00.000000Z"
_TODAY = date(2026, 10, 28)
_PL_HEADER = "氏名,メールアドレス,年月,区分,売上,月給部分の原価,月給の外の手当,賞与"
_BONUS_HEADER = "氏名,メールアドレス,査定対象の半期,支給月,基準額の種類,基準額,賞与額"
_ITEMS = (
    ("BASE_SALARY", 200000),
    ("DUTY_ALLOWANCE", 50000),
    ("FIXED_OVERTIME", 30000),
    ("HOUSING", 10000),
    ("LEADER", 10000),
)
_TARO_ID = 1
_HANAKO_ID = 2


def _csv(header: str, *lines: str) -> bytes:
    """見出しと行から、取込用 CSV（UTF-8、CRLF）のバイト列を作る。"""
    return "\r\n".join((header, *lines)).encode("utf-8") + b"\r\n"


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """マイグレーションを適用し、社員を2人入れた DB を、モジュールで1回だけ作る（複写の元）。"""
    directory = tmp_path_factory.mktemp("migrated")
    path = directory / "template.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, directory / "migration-backups")
        connection.executemany(
            "INSERT INTO employee (id, email, name, enrollment_status, join_date, "
            "created_at, updated_at) VALUES (?, ?, ?, '在籍', '2020-04-01', ?, ?)",
            [
                (_TARO_ID, "taro@example.com", "山田 太郎", _TIMESTAMP, _TIMESTAMP),
                (_HANAKO_ID, "hanako@example.com", "鈴木 花子", _TIMESTAMP, _TIMESTAMP),
            ],
        )
        connection.commit()
    return path


@pytest.fixture
def db_path(tmp_path: Path, migrated_db: Path) -> Path:
    """マイグレーションを適用した一時 DB（複写）を作る。"""
    path = tmp_path / "test.db"
    shutil.copyfile(migrated_db, path)
    return path


@pytest.fixture
def connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """一時 DB への接続。"""
    with open_connection(db_path) as connection:
        yield connection


def _run(
    connection: sqlite3.Connection, db_path: Path, kind: ImportKind, content: bytes
) -> ImportReport:
    """取込の実行を、試験用のバックアップの作成先で呼ぶ。"""
    return run_import(
        connection,
        kind,
        content,
        _TODAY,
        db_path=db_path,
        backup_dir=db_path.parent / "backups",
    )


def _issues(report: ImportReport) -> list[tuple[int | None, str | None, str]]:
    """エラーを、行番号・列名・コードの組にする。"""
    return [(issue.line_number, issue.column, issue.code.value) for issue in report.issues]


def _pl_rows(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """`monthly_pl` を、社員・年月の順に返す。"""
    rows: list[tuple[object, ...]] = connection.execute(
        "SELECT employee_id, year_month, category, sales, cost_salary, cost_allowance, "
        "cost_bonus, cost_total, gross_profit, source, is_closed FROM monthly_pl "
        "ORDER BY employee_id, year_month"
    ).fetchall()
    return rows


def _bonus_rows(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """`bonus` を、社員・半期の順に返す。"""
    rows: list[tuple[object, ...]] = connection.execute(
        "SELECT employee_id, eval_half, pay_month, base_type, base_amount, eval_rank, rate_bp, "
        "bonus_amount, source, is_closed FROM bonus ORDER BY employee_id, eval_half"
    ).fetchall()
    return rows


def _operation_logs(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """操作ログを、記録の順に返す。"""
    rows: list[tuple[object, ...]] = connection.execute(
        "SELECT operation, target_table, detail FROM operation_log ORDER BY id"
    ).fetchall()
    return rows


def _insert_pl(
    connection: sqlite3.Connection,
    year_month: str,
    source: str,
    is_closed: int,
    employee_id: int = _TARO_ID,
) -> None:
    """DB に、既存の `monthly_pl` の行を1件入れる（売上 1、原価 1）。"""
    connection.execute(
        "INSERT INTO monthly_pl (employee_id, year_month, category, sales, cost_salary, "
        "cost_allowance, cost_bonus, cost_total, gross_profit, source, is_closed, "
        "created_at, updated_at) VALUES (?, ?, NULL, 1, 1, 0, 0, 1, 0, ?, ?, ?, ?)",
        (employee_id, year_month, source, is_closed, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()


def _prepare_october_calculation(connection: sqlite3.Connection) -> None:
    """山田太郎の 2026-10 を計算できるようにする。

    給与は月給 300,000（2026-04 から）、10月は全営業日を、売上計上でない待機（減額なし）とする。
    原価（月給部分）は 300,000 × 1.2 = 360,000、売上は 0 になる。
    """
    create_year_calendar(connection, 2026)
    register_salary_history(
        connection,
        _TARO_ID,
        SalaryHistoryInput(
            effective_from=date(2026, 4, 1),
            pay_system="通常評価制度",
            employment_type="正社員",
            grade=None,
            annual_salary=None,
            revision_type="定期（4月）",
            note=None,
            items=tuple(SalaryItemInput(code, amount) for code, amount in _ITEMS),
        ),
    )
    create_work_period(
        connection,
        _TARO_ID,
        WorkPeriodInput(
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 31),
            work_status_code="WAIT_FULL",
            assignment_id=None,
            billing_ratio_percent="100",
            note=None,
        ),
    )


# ---------------------------------------------------------------------------
# IMP-05 月次売上原価（MIG-044）
# ---------------------------------------------------------------------------


_PL_KIND = ImportKind.MONTHLY_PL


def test_tc_mig_032_monthly_pl_is_imported_with_totals(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """原価合計・粗利を計算して保存する。`source` は `取込`、`is_closed` は 1。

    手当・賞与の空は 0、粗利は負の値を取りうる。メールアドレスの行は、氏名より優先する。
    """
    content = _csv(
        _PL_HEADER,
        '山田 太郎,,2026/04,SES,"700,000",420000,30000,50000',
        "別の 氏名,hanako@example.com,2026-5,,100000,200000,,",
    )

    report = _run(connection, db_path, _PL_KIND, content)

    assert report.issues == ()
    assert report.executed is True
    assert (report.imported_count, report.matched_row_count, report.unmatched) == (2, 2, ())
    assert report.recalculation is None
    assert _pl_rows(connection) == [
        (_TARO_ID, "2026-04", "SES", 700000, 420000, 30000, 50000, 500000, 200000, "取込", 1),
        (_HANAKO_ID, "2026-05", None, 100000, 200000, 0, 0, 200000, -100000, "取込", 1),
    ]
    assert _operation_logs(connection) == [
        ("取込", "monthly_pl", "種類=monthly-pl 行数=2 再計算した月数=0 計算エラー=0")
    ]


def test_tc_mig_033_monthly_pl_outside_the_20th_period_is_rejected(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """20期（2026-04〜2027-03）の外の年月は `E018`。何も取り込まない。"""
    content = _csv(
        _PL_HEADER,
        "山田 太郎,,2026/03,SES,1,1,0,0",
        "鈴木 花子,,2027/04,SES,1,1,0,0",
        "鈴木 花子,,2027/03,SES,1,1,0,0",
    )

    report = _run(connection, db_path, _PL_KIND, content)

    assert _issues(report) == [(2, "年月", "E018"), (3, "年月", "E018")]
    assert report.executed is False
    assert _pl_rows(connection) == []
    assert _operation_logs(connection) == []


def test_tc_mig_034_recalculated_open_row_is_replaced(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """再計算で作られた締め前の行（`再計算`、`is_closed` 0）は、置き換える。作成日時は残す。"""
    _insert_pl(connection, "2026-04", "再計算", 0)

    report = _run(
        connection, db_path, _PL_KIND, _csv(_PL_HEADER, "山田 太郎,,2026-04,受託,500,300,0,0")
    )

    assert report.issues == ()
    assert _pl_rows(connection) == [
        (_TARO_ID, "2026-04", "受託", 500, 300, 0, 0, 300, 200, "取込", 1)
    ]
    created_at, updated_at = connection.execute(
        "SELECT created_at, updated_at FROM monthly_pl"
    ).fetchone()
    assert created_at == _TIMESTAMP
    assert updated_at != _TIMESTAMP


@pytest.mark.parametrize(
    ("source", "is_closed"),
    [("取込", 1), ("再計算", 1), ("アプリ計算", 0), ("アプリ計算", 1)],
)
def test_tc_mig_035_closed_or_imported_row_is_rejected(
    connection: sqlite3.Connection, db_path: Path, source: str, is_closed: int
) -> None:
    """再計算で作られた締め前の行でなければ、`E019`。既存の行は変えない。"""
    _insert_pl(connection, "2026-04", source, is_closed)
    before = _pl_rows(connection)

    report = _run(
        connection, db_path, _PL_KIND, _csv(_PL_HEADER, "山田 太郎,,2026-04,SES,500,300,0,0")
    )

    assert _issues(report) == [(2, "年月", "E019")]
    assert _pl_rows(connection) == before


def test_tc_mig_035_importing_the_same_csv_twice_is_rejected(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """取込済みの月を、同じ CSV でもう一度取り込むと `E019`。"""
    content = _csv(_PL_HEADER, "山田 太郎,,2026-04,SES,500,300,0,0")
    assert _run(connection, db_path, _PL_KIND, content).executed is True

    report = _run(connection, db_path, _PL_KIND, content)

    assert _issues(report) == [(2, "年月", "E019")]
    assert len(_operation_logs(connection)) == 1


def test_monthly_pl_row_errors_are_collected_without_values(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """行ごとの検証・名寄せ・ファイル内の重複のエラーを、すべて集める。"""
    content = _csv(
        _PL_HEADER,
        "山田 太郎,,2026-04,SES,500,300,0,0",
        "山田 太郎,,2026-04,SES,500,300,0,0",
        "不明 社員,,2026-13,派遣,¥500,-1,1.5,",
        ",,2026-05,,,300,0,0",
        "鈴木 花子,,,SES,500,,0,0",
    )

    report = _run(connection, db_path, _PL_KIND, content)

    assert _issues(report) == [
        (3, "年月", "E012"),
        (4, "氏名", "E010"),
        (4, "年月", "E007"),
        (4, "区分", "E008"),
        (4, "売上", "E007"),
        (4, "月給部分の原価", "E009"),
        (4, "月給の外の手当", "E007"),
        (5, "氏名", "E006"),
        (5, "売上", "E006"),
        (6, "年月", "E006"),
        (6, "月給部分の原価", "E006"),
    ]
    assert report.matched_row_count == 3
    assert [(person.name, person.line_numbers) for person in report.unmatched] == [
        ("不明 社員", (4,))
    ]
    assert _pl_rows(connection) == []


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("年月,売上,月給部分の原価", [(1, "氏名／メールアドレス", "E003")]),
        ("氏名,年月,月給部分の原価", [(1, "売上", "E003")]),
    ],
)
def test_monthly_pl_missing_columns_are_rejected(
    connection: sqlite3.Connection,
    header: str,
    expected: list[tuple[int | None, str | None, str]],
) -> None:
    """社員の特定の列（氏名・メールアドレスのどちらか）と、必須の列がないときは `E003`。"""
    report = check_import(connection, _PL_KIND, _csv(header, "a,b,c"), _TODAY)

    assert _issues(report) == expected
    assert report.matched_row_count == 0


def test_monthly_pl_check_does_not_change_db(connection: sqlite3.Connection) -> None:
    """確認の実行は、置き換えの対象の行があっても、DB を変えない（`MIG-004`）。"""
    _insert_pl(connection, "2026-04", "再計算", 0)
    before = _pl_rows(connection)

    report = check_import(
        connection, _PL_KIND, _csv(_PL_HEADER, "山田 太郎,,2026-04,SES,500,300,0,0"), _TODAY
    )

    assert (report.issues, report.executed, report.row_count) == ((), False, 1)
    assert _pl_rows(connection) == before
    assert _operation_logs(connection) == []


def test_insert_monthly_pl_refuses_rows_that_cannot_be_replaced(
    connection: sqlite3.Connection,
) -> None:
    """検証の後に、置き換えられない行ができていたら、例外にする（値をメッセージに含めない）。"""
    _insert_pl(connection, "2026-04", "取込", 1)
    record = MonthlyPlRecord(
        line_number=2,
        employee_id=_TARO_ID,
        year_month="2026-04",
        category=None,
        sales=500,
        cost_salary=300,
        cost_allowance=0,
        cost_bonus=0,
    )

    with pytest.raises(MonthlyPlConflictError) as raised:
        insert_monthly_pl(connection, [record])

    assert "2026-04" not in str(raised.value)
    connection.rollback()
    assert _pl_rows(connection)[0][3] == 1


def test_tc_mig_036_imported_monthly_pl_is_kept_by_recalculation_and_reopening(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """取込の行は、再計算で上書きされず、月次締め・締め解除の対象にもならない（`MIG-054`）。

    取込の行は、月次締めの判定に数えない（取込済みの月も、再計算の対象）。
    `monthly_payroll` は計算して保存する。
    """
    _prepare_october_calculation(connection)
    report = _run(
        connection, db_path, _PL_KIND, _csv(_PL_HEADER, "山田 太郎,,2026-10,SES,700,400,0,0")
    )
    assert report.executed is True
    imported = _pl_rows(connection)

    assert is_month_closed(connection, "2026-10") is False
    result = recalculate_range(connection, date(2026, 10, 1), _TODAY, (_TARO_ID,))
    connection.commit()
    assert (result.year_months, result.saved_count, result.errors) == (("2026-10",), 1, ())
    assert _pl_rows(connection) == imported
    payroll_count = connection.execute(
        "SELECT COUNT(*) FROM monthly_payroll WHERE employee_id = ? AND year_month = '2026-10'",
        (_TARO_ID,),
    ).fetchone()[0]
    assert payroll_count == 1

    close_month(connection, "2026-10", acknowledge_errors=True)
    reopen_month(connection, "2026-10")
    recalculate_month(connection, "2026-10")
    connection.commit()

    assert _pl_rows(connection) == imported


# ---------------------------------------------------------------------------
# IMP-06 賞与（MIG-045）
# ---------------------------------------------------------------------------


_BONUS_KIND = ImportKind.BONUS


def test_tc_mig_038_bonus_is_imported_and_reflected_in_the_pay_month_cost(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """`source` は `取込`、`is_closed` は 1、評価ランク・掛け率は `NULL`。支給月の原価に反映する。

    再計算は、支給月の同じ社員の月だけ（`MIG-054`）。
    """
    _prepare_october_calculation(connection)
    content = _csv(_BONUS_HEADER, '山田 太郎,,20期上期,2026/10,基本給,"250,000",375000')

    report = _run(connection, db_path, _BONUS_KIND, content)

    assert report.issues == ()
    assert (report.executed, report.imported_count, report.matched_row_count) == (True, 1, 1)
    assert _bonus_rows(connection) == [
        (_TARO_ID, "2026-H1", "2026-10", "基本給", 250000, None, None, 375000, "取込", 1)
    ]
    assert report.recalculation is not None
    assert report.recalculation.year_months == ("2026-10",)
    assert report.calculation_error_count == 0
    cost_bonus, cost_total, source = connection.execute(
        "SELECT cost_bonus, cost_total, source FROM monthly_pl "
        "WHERE employee_id = ? AND year_month = '2026-10'",
        (_TARO_ID,),
    ).fetchone()
    assert (cost_bonus, cost_total, source) == (375000, 375000 + 360000, "再計算")
    # 準備（給与履歴・稼働期間の登録）の操作ログを除き、取込の操作ログだけを見る。
    assert [log for log in _operation_logs(connection) if log[0] == "取込"] == [
        ("取込", "bonus", "種類=bonus 行数=1 再計算した月数=1 計算エラー=0")
    ]


def test_bonus_recalculates_only_the_pay_month_of_each_employee(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """支給月ごとに、その支給月の社員だけを再計算する。計算エラーがあってもコミットする。

    2025年の営業日カレンダーがなく、鈴木花子の給与履歴もないため、両方とも計算エラーになる。
    """
    create_year_calendar(connection, 2026)
    content = _csv(
        _BONUS_HEADER,
        "鈴木 花子,,19期下期,2026-04,月給合計,300000,300000",
        "山田 太郎,,2025-H1,2025-10,基本給,250000,250000",
    )

    report = _run(connection, db_path, _BONUS_KIND, content)

    assert report.executed is True
    assert report.recalculation is not None
    assert report.recalculation.year_months == ("2025-10", "2026-04")
    assert [
        (error.employee_id, error.year_month, error.reason) for error in report.recalculation.errors
    ] == [
        (_TARO_ID, "2025-10", CalculationErrorReason.NO_BUSINESS_DAY),
        (_HANAKO_ID, "2026-04", CalculationErrorReason.NO_SALARY_HISTORY),
    ]
    assert [row[:3] for row in _bonus_rows(connection)] == [
        (_TARO_ID, "2025-H1", "2025-10"),
        (_HANAKO_ID, "2025-H2", "2026-04"),
    ]


def test_bonus_paid_after_this_month_is_not_recalculated(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """支給月が今月より後なら、取り込むが、再計算しない（`MIG-054`）。"""
    content = _csv(_BONUS_HEADER, "山田 太郎,,2026-H2,2027-04,基本給,250000,250000")

    report = _run(connection, db_path, _BONUS_KIND, content)

    assert report.executed is True
    assert report.recalculated_month_count == 0
    assert len(_bonus_rows(connection)) == 1
    assert _pl_rows(connection) == []


def test_bonus_paid_in_a_closed_month_is_not_recalculated(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """支給月が締め済みの月なら、取り込むが、計算せず `closed_months` に入れる（`MIG-054`）。

    締め済みの月の計算結果（賞与の原価 0）は、変わらない。鈴木花子は給与履歴がなく、計算エラーの
    まま締める。
    """
    _prepare_october_calculation(connection)
    recalculate_month(connection, "2026-10")
    connection.commit()
    close_month(connection, "2026-10", acknowledge_errors=True)
    before = _pl_rows(connection)
    content = _csv(_BONUS_HEADER, "山田 太郎,,2026-H1,2026-10,基本給,250000,375000")

    report = _run(connection, db_path, _BONUS_KIND, content)

    assert report.executed is True
    assert report.recalculation is not None
    assert report.recalculation.year_months == ()
    assert report.recalculation.closed_months == ("2026-10",)
    assert report.recalculated_month_count == 0
    assert [row[:3] for row in _bonus_rows(connection)] == [(_TARO_ID, "2026-H1", "2026-10")]
    assert _pl_rows(connection) == before
    assert [row[6] for row in before] == [0]


def test_tc_mig_037_pay_month_must_match_the_half(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """半期と支給月が対応しない（上期は同年10月、下期は翌年4月）ときは `E016`。"""
    content = _csv(
        _BONUS_HEADER,
        "山田 太郎,,2026-H1,2027-04,基本給,250000,250000",
        "鈴木 花子,,20期下期,2026-11,基本給,250000,250000",
    )

    report = _run(connection, db_path, _BONUS_KIND, content)

    assert _issues(report) == [(2, "支給月", "E016"), (3, "支給月", "E016")]
    assert _bonus_rows(connection) == []
    assert _operation_logs(connection) == []


def test_bonus_duplicate_keys_in_file_and_db_are_rejected(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """主キー（社員、査定対象の半期）が、ファイル内で重複すると `E012`、DB に既にあると `E013`。"""
    assert _run(
        connection,
        db_path,
        _BONUS_KIND,
        _csv(_BONUS_HEADER, "鈴木 花子,,2026-H2,2027-04,基本給,1,1"),
    ).executed

    content = _csv(
        _BONUS_HEADER,
        ",taro@example.com,2026-H2,2027-04,基本給,1,1",
        "山田 太郎,,20期下期,2027-04,基本給,1,1",
        "鈴木 花子,,2026-H2,2027-04,基本給,1,1",
    )
    report = _run(connection, db_path, _BONUS_KIND, content)

    assert _issues(report) == [(3, "査定対象の半期", "E012"), (4, "査定対象の半期", "E013")]
    assert len(_bonus_rows(connection)) == 1


def test_bonus_row_errors_are_collected(connection: sqlite3.Connection, db_path: Path) -> None:
    """行ごとの検証と名寄せのエラーを、すべて集める。"""
    content = _csv(
        _BONUS_HEADER,
        "山田 太郎,,2026上期,2026-10,年俸,-1,",
        "不明 社員,,,,基本給,1,1",
    )

    report = _run(connection, db_path, _BONUS_KIND, content)

    assert _issues(report) == [
        (2, "査定対象の半期", "E007"),
        (2, "基準額の種類", "E008"),
        (2, "基準額", "E009"),
        (2, "賞与額", "E006"),
        (3, "氏名", "E010"),
        (3, "査定対象の半期", "E006"),
        (3, "支給月", "E006"),
    ]
    assert report.matched_row_count == 1
    assert _bonus_rows(connection) == []


def test_employees_by_pay_month_groups_and_sorts() -> None:
    """取込の後の再計算の対象を、支給月の古い順・社員 ID の順にまとめる。"""
    records = [
        BonusRecord(2, 3, "2026-H1", "2026-10", "基本給", 1, 1),
        BonusRecord(3, 1, "2025-H2", "2026-04", "基本給", 1, 1),
        BonusRecord(4, 2, "2026-H1", "2026-10", "基本給", 1, 1),
    ]

    assert employees_by_pay_month(records) == (("2026-04", (1,)), ("2026-10", (2, 3)))


def test_import_kinds_have_labels() -> None:
    """取込の種類の識別子と、画面の名称（`MIG-010`）。"""
    assert (ImportKind("monthly-pl").label, ImportKind("bonus").label) == ("月次売上原価", "賞与")
