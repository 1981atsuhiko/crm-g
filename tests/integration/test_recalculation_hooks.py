"""更新ユースケースから再計算への接続の試験（`ARC-009`、`UID-054`、`UID-055`）。

`TC-SCN-009`、`TC-FUN-009`、`TC-FUN-014`、`TC-ARC-015` を含む。
"""

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application import recalculation
from crm_g.application.assignment import AssignmentInput, create_assignment, update_assignment
from crm_g.application.business_calendar import (
    create_year_calendar,
    refetch_holidays,
    register_manual_day,
)
from crm_g.application.employee import EmployeeInput, create_employee, update_employee
from crm_g.application.monthly_close import close_month
from crm_g.application.pay_item import PayItemInput, get_pay_item, update_pay_item
from crm_g.application.recalculation import (
    RecalculationResult,
    earliest_changed_date,
    recalculate_month,
)
from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.application.system_setting import change_coefficient
from crm_g.application.unit_price import (
    RateHistoryInput,
    delete_rate_history,
    register_rate_history,
    update_rate_history,
)
from crm_g.application.work_period import (
    StatusSwitchInput,
    WorkPeriodInput,
    create_work_period,
    switch_work_status,
    update_work_period,
)
from crm_g.application.work_status_rate import change_rate
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"


class _Listener:
    """`on_recalculated` に渡し、受け取った再計算の結果を記録する。"""

    def __init__(self) -> None:
        self.results: list[RecalculationResult] = []

    def __call__(self, result: RecalculationResult) -> None:
        self.results.append(result)

    @property
    def months(self) -> tuple[str, ...]:
        assert len(self.results) == 1
        return self.results[0].year_months


def _employee(**overrides: object) -> EmployeeInput:
    values: dict[str, object] = {
        "email": "taro@example.com",
        "name": "山田太郎",
        "name_kana": None,
        "employee_no": None,
        "employment_type": None,
        "birth_date": None,
        "gender": None,
        "join_date": date(2020, 4, 1),
        "nearest_station": None,
        "residence_area": None,
        "job_type": None,
        "enrollment_status": "在籍",
        "retire_date": None,
    }
    values.update(overrides)
    return EmployeeInput(**values)  # type: ignore[arg-type]


def _salary(effective_from: date, base: int) -> SalaryHistoryInput:
    return SalaryHistoryInput(
        effective_from=effective_from,
        pay_system="通常評価制度",
        employment_type="正社員",
        grade=None,
        annual_salary=None,
        revision_type="その他",
        note=None,
        items=(SalaryItemInput("BASE_SALARY", base),),
    )


def _period(
    start: date, status: str = "WAIT_FULL", assignment: int | None = None
) -> WorkPeriodInput:
    return WorkPeriodInput(
        start_date=start,
        end_date=None,
        work_status_code=status,
        assignment_id=assignment,
        billing_ratio_percent="100",
        note=None,
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """社員1人（10月・11月は計算済み、10月は締め済みにできる状態）。"""
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        create_year_calendar(connection, 2026)
        create_employee(connection, _employee())
        register_salary_history(connection, 1, _salary(date(2026, 4, 1), 300000))
        create_work_period(connection, 1, _period(date(2026, 4, 1)))
        recalculate_month(connection, "2026-10")
        recalculate_month(connection, "2026-11")
        connection.commit()
        yield connection


def _salary_of(connection: sqlite3.Connection, year_month: str) -> object:
    row = connection.execute(
        "SELECT monthly_salary FROM monthly_payroll WHERE employee_id = 1 AND year_month = ?",
        (year_month,),
    ).fetchone()
    return None if row is None else row[0]


def _paid_of(connection: sqlite3.Connection, year_month: str) -> object:
    return connection.execute(
        "SELECT paid_amount FROM monthly_payroll WHERE employee_id = 1 AND year_month = ?",
        (year_month,),
    ).fetchone()[0]


def test_earliest_changed_date() -> None:
    assert earliest_changed_date([(date(2026, 4, 1), date(2026, 4, 1))]) is None
    assert earliest_changed_date(
        [(date(2026, 4, 1), date(2026, 5, 1)), (None, date(2026, 9, 30))]
    ) == date(2026, 4, 1)
    assert earliest_changed_date([(date(2026, 9, 30), None)]) == date(2026, 9, 30)


def test_tc_scn_009_salary_history_registration_recalculates(
    connection: sqlite3.Connection,
) -> None:
    listener = _Listener()

    register_salary_history(
        connection, 1, _salary(date(2026, 11, 1), 320000), on_recalculated=listener
    )

    assert listener.months == ("2026-11",)
    assert (_salary_of(connection, "2026-10"), _salary_of(connection, "2026-11")) == (
        300000,
        320000,
    )
    end = connection.execute(
        "SELECT effective_to FROM salary_history WHERE effective_from = '2026-04-01'"
    ).fetchone()
    assert end == ("2026-10-31",)


def test_tc_fun_009_join_date_change_recalculates_open_months_and_warns_closed(
    connection: sqlite3.Connection,
) -> None:
    close_month(connection, "2026-10")
    paid_october = _paid_of(connection, "2026-10")
    listener = _Listener()

    update_employee(
        connection, 1, _employee(join_date=date(2026, 10, 16)), on_recalculated=listener
    )

    result = listener.results[0]
    assert result.year_months == ("2026-11",)
    assert result.closed_months == ("2026-10",)
    assert _paid_of(connection, "2026-10") == paid_october


def test_employee_change_without_date_change_does_not_recalculate(
    connection: sqlite3.Connection,
) -> None:
    listener = _Listener()

    update_employee(connection, 1, _employee(name="山田次郎"), on_recalculated=listener)

    assert listener.results == []


def test_new_employee_is_calculated_in_calculated_months(connection: sqlite3.Connection) -> None:
    listener = _Listener()

    new_id = create_employee(
        connection,
        _employee(email="hanako@example.com", name="鈴木花子", join_date=date(2026, 11, 1)),
        on_recalculated=listener,
    )

    # 給与履歴がまだないため、計算済みの11月は計算エラーになる（結果は保存しない）。
    assert listener.months == ("2026-11",)
    assert [error.employee_id for error in listener.results[0].errors] == [new_id]


def test_tc_fun_014_company_holiday_recalculates_with_fewer_business_days(
    connection: sqlite3.Connection,
) -> None:
    # 11/16 入社として、11月を日割りにする（支払額 = 月給 × 在籍営業日 ÷ 営業日）。
    connection.execute("UPDATE employee SET join_date = '2026-11-16' WHERE id = 1")
    connection.commit()
    recalculate_month(connection, "2026-11")
    connection.commit()
    assert _paid_of(connection, "2026-11") == 157895  # 300,000 × 10 ÷ 19
    listener = _Listener()

    register_manual_day(connection, date(2026, 11, 2), False, "会社休日", on_recalculated=listener)

    assert listener.months == ("2026-11",)
    assert _paid_of(connection, "2026-11") == 166667  # 300,000 × 10 ÷ 18


def test_tc_arc_015_failure_in_recalculation_rolls_back_the_update(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(**_: object) -> object:
        raise RuntimeError("再計算の途中で失敗")

    monkeypatch.setattr(recalculation, "calculate_payroll", _fail)

    with pytest.raises(RuntimeError):
        register_salary_history(connection, 1, _salary(date(2026, 11, 1), 320000))

    count = connection.execute("SELECT COUNT(*) FROM salary_history").fetchone()[0]
    assert count == 1
    assert _salary_of(connection, "2026-11") == 300000
    assert not connection.in_transaction


@pytest.mark.parametrize("operation", ["create", "update", "switch"])
def test_work_period_changes_recalculate(connection: sqlite3.Connection, operation: str) -> None:
    listener = _Listener()
    if operation == "create":
        connection.execute("UPDATE work_period SET end_date = '2026-10-31'")
        connection.commit()
        create_work_period(
            connection, 1, _period(date(2026, 11, 1), "TRN_FULL"), on_recalculated=listener
        )
    elif operation == "update":
        update_work_period(
            connection, 1, _period(date(2026, 4, 1), "WAIT_CUT"), on_recalculated=listener
        )
    else:
        switch_work_status(
            connection,
            1,
            StatusSwitchInput(
                switch_date=date(2026, 11, 16),
                work_status_code="WAIT_CUT",
                assignment_id=None,
                billing_ratio_percent="100",
                note=None,
            ),
            on_recalculated=listener,
        )

    expected = ("2026-10", "2026-11") if operation == "update" else ("2026-11",)
    assert listener.months == expected


def _billable_setup(connection: sqlite3.Connection) -> tuple[int, int]:
    assignment = create_assignment(
        connection,
        1,
        AssignmentInput(
            customer_name="A社",
            project_name=None,
            category="SES",
            start_date=date(2026, 11, 1),
            end_date=None,
        ),
    )
    rate = register_rate_history(
        connection,
        assignment,
        RateHistoryInput(
            effective_from=date(2026, 11, 1), unit_price=800000, revision_basis=None, note=None
        ),
    )
    switch_work_status(
        connection,
        1,
        StatusSwitchInput(
            switch_date=date(2026, 11, 1),
            work_status_code="DEV",
            assignment_id=assignment,
            billing_ratio_percent="100",
            note=None,
        ),
    )
    return assignment, rate


def _sales(connection: sqlite3.Connection) -> object:
    return connection.execute(
        "SELECT sales FROM monthly_pl WHERE employee_id = 1 AND year_month = '2026-11'"
    ).fetchone()[0]


def test_unit_price_changes_recalculate(connection: sqlite3.Connection) -> None:
    assignment, rate = _billable_setup(connection)
    assert _sales(connection) == 800000

    listener = _Listener()
    update_rate_history(
        connection,
        rate,
        RateHistoryInput(
            effective_from=date(2026, 11, 1), unit_price=900000, revision_basis=None, note=None
        ),
        on_recalculated=listener,
    )
    assert listener.months == ("2026-11",)
    assert _sales(connection) == 900000

    second = _Listener()
    register_rate_history(
        connection,
        assignment,
        RateHistoryInput(
            effective_from=date(2026, 11, 16), unit_price=0, revision_basis=None, note=None
        ),
        on_recalculated=second,
    )
    assert second.months == ("2026-11",)

    third = _Listener()
    delete_rate_history(connection, rate, on_recalculated=third)
    # 11/1〜11/15 の単価がなくなり、計算エラーになる。
    assert [error.reason.value for error in third.results[0].errors] == [
        "売上計上の営業日に単価がない"
    ]


def test_assignment_category_change_recalculates(connection: sqlite3.Connection) -> None:
    assignment, _ = _billable_setup(connection)
    listener = _Listener()

    update_assignment(
        connection,
        assignment,
        AssignmentInput(
            customer_name="A社",
            project_name=None,
            category="受託",
            start_date=date(2026, 11, 1),
            end_date=None,
        ),
        on_recalculated=listener,
    )

    assert listener.months == ("2026-11",)
    assert connection.execute(
        "SELECT category FROM monthly_pl WHERE year_month = '2026-11'"
    ).fetchone() == ("受託",)


def test_assignment_change_without_category_change_does_not_recalculate(
    connection: sqlite3.Connection,
) -> None:
    assignment, _ = _billable_setup(connection)
    listener = _Listener()

    update_assignment(
        connection,
        assignment,
        AssignmentInput(
            customer_name="A社（改称）",
            project_name=None,
            category="SES",
            start_date=date(2026, 11, 1),
            end_date=None,
        ),
        on_recalculated=listener,
    )

    assert listener.results == []


def test_master_changes_recalculate_all_employees(connection: sqlite3.Connection) -> None:
    rate_listener = _Listener()
    change_rate(connection, "WAIT_FULL", "90", date(2026, 11, 1), on_recalculated=rate_listener)
    assert rate_listener.months == ("2026-11",)
    assert _paid_of(connection, "2026-11") == 270000

    coefficient_listener = _Listener()
    change_coefficient(connection, "1.5", date(2026, 10, 1), on_recalculated=coefficient_listener)
    assert coefficient_listener.months == ("2026-10", "2026-11")

    item_listener = _Listener()
    item = get_pay_item(connection, "BASE_SALARY")
    assert item is not None
    update_pay_item(
        connection,
        "BASE_SALARY",
        PayItemInput(
            name="基本給（改）",
            category=item.category,
            in_monthly_salary=item.in_monthly_salary,
            cost_direct=item.cost_direct,
            deduction_order=item.deduction_order,
            sort_order=item.sort_order,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
        ),
        on_recalculated=item_listener,
    )
    assert item_listener.months == ("2026-10", "2026-11")

    calendar_listener = _Listener()
    refetch_holidays(connection, 2026, on_recalculated=calendar_listener)
    assert calendar_listener.months == ("2026-10", "2026-11")


def test_creating_a_calendar_year_recalculates_that_year(connection: sqlite3.Connection) -> None:
    listener = _Listener()

    create_year_calendar(connection, 2027, on_recalculated=listener)

    # 2027年の計算済みの月はないため、再計算する月はない。
    assert listener.months == ()


def test_listener_is_called_after_commit(connection: sqlite3.Connection) -> None:
    states: list[bool] = []

    register_salary_history(
        connection,
        1,
        _salary(date(2026, 11, 1), 320000),
        on_recalculated=lambda _result: states.append(connection.in_transaction),
    )

    assert states == [False]


def test_listener_is_not_called_when_the_update_fails(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(**_: object) -> object:
        raise RuntimeError("再計算の途中で失敗")

    monkeypatch.setattr(recalculation, "calculate_payroll", _fail)
    listener = _Listener()

    with pytest.raises(RuntimeError):
        register_salary_history(
            connection, 1, _salary(date(2026, 11, 1), 320000), on_recalculated=listener
        )

    assert listener.results == []


def test_closed_months_are_limited_to_the_changed_employee(
    connection: sqlite3.Connection,
) -> None:
    close_month(connection, "2026-10")
    # 社員2は、10月の結果を持たない（11月入社）。
    other = create_employee(
        connection,
        _employee(email="hanako@example.com", name="鈴木花子", join_date=date(2026, 11, 1)),
    )
    listener = _Listener()

    update_employee(
        connection,
        other,
        _employee(email="hanako@example.com", name="鈴木花子", join_date=date(2026, 11, 2)),
        on_recalculated=listener,
    )

    assert listener.results[0].closed_months == ()
