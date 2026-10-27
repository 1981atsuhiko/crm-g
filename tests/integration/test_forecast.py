"""見込みの試験（`SCN-051` の見込み、`TC-FUN-013`、`CLC-023`）。"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from crm_g.application.forecast import (
    ConfidenceTotal,
    ForecastInput,
    InvalidForecastError,
    forecast_sales,
    list_forecasts,
    save_forecast,
    total_by_confidence,
)
from crm_g.application.operation_log import Operation
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """社員1：A（カナ「イ」）、社員2：B（カナ「ア」）。"""
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        for employee_id, name, kana in ((1, "A", "イ"), (2, "B", "ア")):
            connection.execute(
                "INSERT INTO employee (id, email, name, name_kana, enrollment_status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, '在籍', ?, ?)",
                (employee_id, f"e{employee_id}@example.com", name, kana, _TIMESTAMP, _TIMESTAMP),
            )
        connection.commit()
        yield connection


def _input(
    employee_id: int = 1,
    year_month: str = "2026-11",
    confidence: str = "高",
    unit_price: str = "600000",
    ratio: str = "70",
) -> ForecastInput:
    return ForecastInput(employee_id, year_month, confidence, unit_price, ratio)


def _logs(connection: sqlite3.Connection) -> list[tuple[str, str, object, object]]:
    return [
        (
            operation,
            target_key,
            json.loads(before) if before else None,
            json.loads(after) if after else None,
        )
        for operation, target_key, before, after in connection.execute(
            "SELECT operation, target_key, before_value, after_value FROM operation_log "
            "WHERE target_table = 'forecast' ORDER BY id"
        ).fetchall()
    ]


def test_tc_fun_013_register_then_update(connection: sqlite3.Connection) -> None:
    assert save_forecast(connection, _input()) == Operation.CREATE
    assert save_forecast(connection, _input(confidence="確定")) == Operation.UPDATE

    rows = list_forecasts(connection, "2026-11", "2026-11")
    assert len(rows) == 1
    assert (rows[0].confidence, rows[0].unit_price, rows[0].billing_ratio_bp) == (
        "確定",
        600000,
        7000,
    )
    assert rows[0].forecast_sales == 420000
    assert _logs(connection) == [
        (
            "登録",
            "1:2026-11",
            None,
            {"confidence": "高", "forecast_unit_price": 600000, "forecast_billing_ratio_bp": 7000},
        ),
        ("更新", "1:2026-11", {"confidence": "高"}, {"confidence": "確定"}),
    ]


def test_blank_unit_price_or_ratio_has_no_forecast_sales(connection: sqlite3.Connection) -> None:
    save_forecast(connection, _input(unit_price=""))
    save_forecast(connection, _input(employee_id=2, ratio=" "))

    rows = list_forecasts(connection, "2026-11", "2026-11")

    assert [(row.unit_price, row.billing_ratio_bp, row.forecast_sales) for row in rows] == [
        (600000, None, None),
        (None, 7000, None),
    ]


def test_list_is_limited_to_the_range_and_ordered(connection: sqlite3.Connection) -> None:
    save_forecast(connection, _input(year_month="2026-12"))
    save_forecast(connection, _input(employee_id=1, year_month="2026-11"))
    save_forecast(connection, _input(employee_id=2, year_month="2026-11"))
    save_forecast(connection, _input(year_month="2027-01"))

    rows = list_forecasts(connection, "2026-11", "2026-12")

    assert [(row.year_month, row.employee_name) for row in rows] == [
        ("2026-11", "B"),
        ("2026-11", "A"),
        ("2026-12", "A"),
    ]


def test_total_by_confidence(connection: sqlite3.Connection) -> None:
    save_forecast(connection, _input(year_month="2026-11"))
    save_forecast(connection, _input(year_month="2026-12"))
    save_forecast(connection, _input(employee_id=2, unit_price=""))
    save_forecast(connection, _input(employee_id=2, year_month="2026-12", confidence="待機"))

    totals = total_by_confidence(list_forecasts(connection, "2026-11", "2026-12"))

    assert totals == (
        ConfidenceTotal("確定", 0, 0),
        ConfidenceTotal("高", 2, 840000),
        ConfidenceTotal("低", 0, 0),
        ConfidenceTotal("待機", 1, 420000),
    )


def test_forecast_sales_rounds_up() -> None:
    # 333,333 × 33.33% = 111,099.8889 → 111,100
    assert forecast_sales(333333, 3333) == 111100
    assert forecast_sales(None, 5000) is None
    assert forecast_sales(100, None) is None


def test_unit_price_with_commas(connection: sqlite3.Connection) -> None:
    save_forecast(connection, _input(unit_price="600,000"))

    assert list_forecasts(connection, "2026-11", "2026-11")[0].unit_price == 600000


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_input(confidence="中"), "確度は"),
        (_input(unit_price="-1"), "見込み単価は"),
        (_input(unit_price="abc"), "見込み単価は"),
        (_input(ratio="101"), "0〜100"),
        (_input(ratio="70.001"), "小数第2位まで"),
        (_input(ratio="abc"), "数値で"),
        (_input(employee_id=99), "社員が見つかりません"),
    ],
)
def test_invalid_input(connection: sqlite3.Connection, data: ForecastInput, message: str) -> None:
    with pytest.raises(InvalidForecastError, match=message):
        save_forecast(connection, data)

    assert list_forecasts(connection, "2000-01", "2099-12") == ()


def test_saving_unchanged_values_records_nothing(connection: sqlite3.Connection) -> None:
    save_forecast(connection, _input())

    assert save_forecast(connection, _input(unit_price="600,000", ratio="70.00")) is None
    assert [log[0] for log in _logs(connection)] == ["登録"]
