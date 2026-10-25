"""営業日カレンダー（`SCN-060`、`ARC-012`、`DBD-016`）。

祝日は `infrastructure.holidays` から取得する。土日と祝日を非営業日とし、
`source` を `自動取込` にする。会社休日など、画面から登録した日は `手動登録` に
なり、祝日の再取込では上書きしない。

変更は、操作ログに記録する（`SCD-020`）。年の作成は、365日分の行を1回の操作で
作るため、1年につき `登録` を1件だけ記録する（`target_key` は年、`detail` は件数）。
祝日の再取込は、値が変わった日だけを、日ごとに `更新` として記録する。
"""

import calendar
import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from dataclasses import dataclass
from datetime import date, timedelta

from crm_g.application.operation_log import (
    Operation,
    OperationLogDetail,
    changed_values,
    record_operation_log,
)
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction
from crm_g.infrastructure.holidays import year_holidays

_SOURCE_AUTOMATIC = "自動取込"
_SOURCE_MANUAL = "手動登録"

_WEEKEND_WEEKDAYS = (5, 6)  # date.weekday()：土曜=5、日曜=6


class CalendarAlreadyExistsError(RuntimeError):
    """指定した年の行が、既に作成されているときのエラー。"""


def _dates_in_year(year: int) -> list[date]:
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _is_expected_business_day(day: date, holidays: frozenset[date]) -> bool:
    return day.weekday() not in _WEEKEND_WEEKDAYS and day not in holidays


def year_exists(connection: sqlite3.Connection, year: int) -> bool:
    """指定した年の行が、既に1行でも存在するかを返す。"""
    row = connection.execute(
        "SELECT 1 FROM business_calendar WHERE calendar_date BETWEEN ? AND ? LIMIT 1",
        (date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat()),
    ).fetchone()
    return row is not None


def create_year_calendar(connection: sqlite3.Connection, year: int) -> None:
    """指定した年の行を作成する。土日・祝日を非営業日、それ以外を営業日とする。"""
    with transaction(connection):
        if year_exists(connection, year):
            raise CalendarAlreadyExistsError(f"{year}年の営業日カレンダーは、既に作成されています")

        holidays = year_holidays(year)
        now = to_iso8601(utc_now())
        days = _dates_in_year(year)
        connection.executemany(
            "INSERT INTO business_calendar "
            "(calendar_date, is_business_day, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    day.isoformat(),
                    int(_is_expected_business_day(day, holidays)),
                    _SOURCE_AUTOMATIC,
                    now,
                    now,
                )
                for day in days
            ],
        )
        record_operation_log(
            connection,
            Operation.CREATE,
            "business_calendar",
            OperationLogDetail(target_key=str(year), detail=f"{len(days)}件"),
        )


def refetch_holidays(connection: sqlite3.Connection, year: int) -> None:
    """`自動取込`の日だけ、祝日を取得し直して反映する。`手動登録`の日は変更しない。"""
    with transaction(connection):
        holidays = year_holidays(year)
        now = to_iso8601(utc_now())
        rows = connection.execute(
            "SELECT calendar_date, is_business_day FROM business_calendar "
            "WHERE calendar_date BETWEEN ? AND ? AND source = ? ORDER BY calendar_date",
            (date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat(), _SOURCE_AUTOMATIC),
        ).fetchall()
        for calendar_date, current_value in rows:
            expected_value = int(
                _is_expected_business_day(date.fromisoformat(calendar_date), holidays)
            )
            if expected_value == current_value:
                continue
            connection.execute(
                "UPDATE business_calendar SET is_business_day = ?, updated_at = ? "
                "WHERE calendar_date = ? AND source = ?",
                (expected_value, now, calendar_date, _SOURCE_AUTOMATIC),
            )
            record_operation_log(
                connection,
                Operation.UPDATE,
                "business_calendar",
                OperationLogDetail(
                    target_key=calendar_date,
                    before_value={"is_business_day": current_value},
                    after_value={"is_business_day": expected_value},
                ),
            )


def register_manual_day(
    connection: sqlite3.Connection, day: date, is_business_day: bool, note: str | None
) -> None:
    """日を手動で登録する（会社休日、土曜の出勤など）。登録した日は`手動登録`になる。"""
    now = to_iso8601(utc_now())
    values = {"is_business_day": int(is_business_day), "source": _SOURCE_MANUAL, "note": note}
    with transaction(connection):
        existing = connection.execute(
            "SELECT is_business_day, source, note FROM business_calendar WHERE calendar_date = ?",
            (day.isoformat(),),
        ).fetchone()
        connection.execute(
            "INSERT INTO business_calendar "
            "(calendar_date, is_business_day, source, note, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (calendar_date) DO UPDATE SET "
            "is_business_day = excluded.is_business_day, "
            "source = excluded.source, "
            "note = excluded.note, "
            "updated_at = excluded.updated_at",
            (day.isoformat(), values["is_business_day"], values["source"], note, now, now),
        )
        if existing is None:
            record_operation_log(
                connection,
                Operation.CREATE,
                "business_calendar",
                OperationLogDetail(target_key=day.isoformat(), after_value=values),
            )
            return
        before_value, after_value = changed_values(
            dict(zip(("is_business_day", "source", "note"), existing, strict=True)), values
        )
        if after_value:
            record_operation_log(
                connection,
                Operation.UPDATE,
                "business_calendar",
                OperationLogDetail(
                    target_key=day.isoformat(), before_value=before_value, after_value=after_value
                ),
            )


@dataclass(frozen=True)
class CalendarDay:
    """営業日カレンダーの1日分の行。"""

    calendar_date: date
    is_business_day: bool
    source: str
    note: str | None


def list_month(connection: sqlite3.Connection, year: int, month: int) -> tuple[CalendarDay, ...]:
    """指定した年月の行を、日付の昇順で返す。"""
    last_day = calendar.monthrange(year, month)[1]
    rows = connection.execute(
        "SELECT calendar_date, is_business_day, source, note FROM business_calendar "
        "WHERE calendar_date BETWEEN ? AND ? ORDER BY calendar_date",
        (date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()),
    ).fetchall()
    return tuple(
        CalendarDay(
            calendar_date=date.fromisoformat(row[0]),
            is_business_day=bool(row[1]),
            source=row[2],
            note=row[3],
        )
        for row in rows
    )


def month_business_day_count(connection: sqlite3.Connection, year: int, month: int) -> int:
    """指定した年月の営業日数を返す。"""
    last_day = calendar.monthrange(year, month)[1]
    row = connection.execute(
        "SELECT COUNT(*) FROM business_calendar "
        "WHERE calendar_date BETWEEN ? AND ? AND is_business_day = 1",
        (date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()),
    ).fetchone()
    return int(row[0])
