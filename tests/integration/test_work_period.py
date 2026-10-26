"""稼働期間の試験（`SCN-050`、`TBL-016`、`TC-DBD-010`、`TC-DBD-011`、`SCD-020`）。"""

import json
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.assignment import AssignmentInput, create_assignment
from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.work_period import (
    AssignmentNotOwnedError,
    EmployeeNotFoundError,
    InvalidBillingRatioError,
    InvalidPeriodError,
    InvalidSwitchDateError,
    InvalidWorkStatusError,
    MissingAssignmentError,
    NoOpenPeriodError,
    PeriodOverlapError,
    StatusSwitchInput,
    WorkPeriodInput,
    WorkPeriodNotFoundError,
    create_work_period,
    list_month_grid,
    list_work_periods,
    list_work_statuses,
    switch_work_status,
    update_work_period,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-25T00:00:00.000000Z"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) VALUES "
            "(1, 'taro@example.com', '山田太郎', '在籍', '2020-04-01', ?, ?), "
            "(2, 'hanako@example.com', '鈴木花子', '在籍', '2021-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP, _TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
    return path


def _assignment(db_path: Path, employee_id: int = 1) -> int:
    with open_connection(db_path) as connection:
        return create_assignment(
            connection,
            employee_id,
            AssignmentInput(
                customer_name="A社",
                project_name=None,
                category="SES",
                start_date=date(2026, 4, 1),
                end_date=None,
            ),
        )


def _period(**overrides: object) -> WorkPeriodInput:
    values: dict[str, object] = {
        "start_date": date(2026, 10, 1),
        "end_date": None,
        "work_status_code": "WAIT_FULL",
        "assignment_id": None,
        "billing_ratio_percent": "100.00",
        "note": None,
    }
    values.update(overrides)
    return WorkPeriodInput(**values)  # type: ignore[arg-type]


def test_list_work_statuses_in_sort_order(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        statuses = list_work_statuses(connection)

    assert statuses[0].code == "DEV"
    assert statuses[0].is_billable
    assert len(statuses) == 9


def test_create_billable_period_with_assignment_and_log(db_path: Path) -> None:
    assignment_id = _assignment(db_path)
    with open_connection(db_path) as connection:
        new_id = create_work_period(
            connection,
            1,
            _period(
                work_status_code="DEV", assignment_id=assignment_id, billing_ratio_percent="70.5"
            ),
        )
        period = list_work_periods(connection, 1)[0]
        log = connection.execute(
            "SELECT operation, target_key, after_value FROM operation_log "
            "WHERE target_table = 'work_period'"
        ).fetchone()

    assert (period.work_status_name, period.assignment_label, period.billing_ratio_bp) == (
        "開発稼働",
        "A社",
        7050,
    )
    assert log[:2] == ("登録", str(new_id))
    assert json.loads(log[2]) == {
        "employee_id": 1,
        "start_date": "2026-10-01",
        "end_date": None,
        "work_status_code": "DEV",
        "assignment_id": assignment_id,
        "billing_ratio_bp": 7050,
        "note": None,
    }


def test_tc_dbd_011_billable_status_without_assignment_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        with pytest.raises(MissingAssignmentError):
            create_work_period(connection, 1, _period(work_status_code="DEV"))
        assert list_work_periods(connection, 1) == ()


def test_non_billable_status_does_not_keep_assignment(db_path: Path) -> None:
    assignment_id = _assignment(db_path)
    with open_connection(db_path) as connection:
        create_work_period(connection, 1, _period(assignment_id=assignment_id))

        assert list_work_periods(connection, 1)[0].assignment_id is None


def test_other_employees_assignment_is_rejected(db_path: Path) -> None:
    other_assignment = _assignment(db_path, employee_id=2)
    with open_connection(db_path) as connection, pytest.raises(AssignmentNotOwnedError):
        create_work_period(
            connection, 1, _period(work_status_code="DEV", assignment_id=other_assignment)
        )


@pytest.mark.parametrize(
    ("existing_end", "new_start", "new_end"),
    [
        (None, date(2026, 12, 1), None),
        (date(2026, 10, 31), date(2026, 10, 31), date(2026, 11, 30)),
        (date(2026, 10, 31), date(2026, 9, 1), None),
    ],
)
def test_tc_dbd_010_overlapping_period_is_rejected(
    db_path: Path, existing_end: date | None, new_start: date, new_end: date | None
) -> None:
    with open_connection(db_path) as connection:
        create_work_period(connection, 1, _period(end_date=existing_end))

        with pytest.raises(PeriodOverlapError):
            create_work_period(connection, 1, _period(start_date=new_start, end_date=new_end))
        assert len(list_work_periods(connection, 1)) == 1


def test_adjacent_periods_and_other_employees_period_do_not_overlap(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_work_period(connection, 1, _period(end_date=date(2026, 10, 31)))
        create_work_period(connection, 1, _period(start_date=date(2026, 11, 1)))
        create_work_period(connection, 2, _period())

        assert len(list_work_periods(connection, 1)) == 2


def test_update_excludes_own_period_from_overlap_and_logs_changes(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        period_id = create_work_period(connection, 1, _period())
        update_work_period(
            connection, period_id, _period(end_date=date(2026, 12, 31), note="11/10入場")
        )

        period = list_work_periods(connection, 1)[0]
        log = connection.execute(
            "SELECT before_value, after_value FROM operation_log WHERE operation = '更新'"
        ).fetchone()

    assert (period.end_date, period.note) == (date(2026, 12, 31), "11/10入場")
    assert json.loads(log[0]) == {"end_date": None, "note": None}
    assert json.loads(log[1]) == {"end_date": "2026-12-31", "note": "11/10入場"}


def test_update_rejects_overlap_with_another_period(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        first = create_work_period(connection, 1, _period(end_date=date(2026, 10, 31)))
        create_work_period(connection, 1, _period(start_date=date(2026, 11, 1)))

        with pytest.raises(PeriodOverlapError):
            update_work_period(connection, first, _period(end_date=date(2026, 11, 1)))


def test_update_without_changes_logs_nothing(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        period_id = create_work_period(connection, 1, _period())
        update_work_period(connection, period_id, _period())

        count = connection.execute(
            "SELECT COUNT(*) FROM operation_log WHERE operation = '更新'"
        ).fetchone()[0]
    assert count == 0


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"end_date": date(2026, 9, 30)}, InvalidPeriodError),
        ({"billing_ratio_percent": "70.001"}, InvalidBillingRatioError),
        ({"billing_ratio_percent": "101"}, InvalidBillingRatioError),
        ({"billing_ratio_percent": "abc"}, InvalidBillingRatioError),
        ({"work_status_code": "UNKNOWN"}, InvalidWorkStatusError),
    ],
)
def test_create_rejects_invalid_input(
    db_path: Path, overrides: dict[str, object], error: type[Exception]
) -> None:
    with open_connection(db_path) as connection, pytest.raises(error):
        create_work_period(connection, 1, _period(**overrides))


def test_create_rejects_unknown_employee(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(EmployeeNotFoundError):
        create_work_period(connection, 999, _period())


def test_update_rejects_unknown_period(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(WorkPeriodNotFoundError):
        update_work_period(connection, 999, _period())


def test_month_grid_joins_statuses_and_marks_unset_days(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)
        create_year_calendar(connection, 2027)
        create_work_period(
            connection, 1, _period(start_date=date(2026, 10, 15), end_date=date(2026, 10, 31))
        )
        create_work_period(
            connection, 1, _period(start_date=date(2026, 11, 1), work_status_code="TRN_FULL")
        )

        grid = list_month_grid(connection, 2026, 2, ("在籍",), search="山田")

    assert [row.employee_name for row in grid] == ["山田太郎"]
    assert grid[0].cells[0] == "待機（減額無し）（未設定あり）"
    assert grid[0].cells[1] == "社内研修（減額なし）"
    assert grid[0].cells[5] == "社内研修（減額なし）"


def test_month_grid_search_matches_name_only(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assert list_month_grid(connection, 2026, 2, ("在籍",), search="taro") == ()


def test_update_to_non_billable_status_clears_assignment(db_path: Path) -> None:
    assignment_id = _assignment(db_path)
    with open_connection(db_path) as connection:
        period_id = create_work_period(
            connection, 1, _period(work_status_code="DEV", assignment_id=assignment_id)
        )
        update_work_period(connection, period_id, _period(assignment_id=assignment_id))

        assert list_work_periods(connection, 1)[0].assignment_id is None


def test_month_grid_respects_join_date_and_missing_calendar(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)
        # 10月の途中に入社した社員。入社前の営業日は、未設定として数えない。
        connection.execute("UPDATE employee SET join_date = '2026-10-15' WHERE id = 1")
        connection.commit()
        create_work_period(connection, 1, _period(start_date=date(2026, 10, 15)))

        grid = list_month_grid(connection, 2026, 2, ("在籍",), search="山田")

    assert grid[0].cells[0] == "待機（減額無し）"
    # 2027年の営業日カレンダーがない月は、未設定を判定しない。
    assert grid[0].cells[3] == "待機（減額無し）"


def test_month_grid_search_ignores_letter_case(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        connection.execute("UPDATE employee SET name = 'Taro Yamada' WHERE id = 1")
        connection.commit()

        grid = list_month_grid(connection, 2026, 2, ("在籍",), search="taro")

    assert [row.employee_name for row in grid] == ["Taro Yamada"]


def _switch(**overrides: object) -> StatusSwitchInput:
    values: dict[str, object] = {
        "switch_date": date(2026, 11, 10),
        "work_status_code": "TRN_FULL",
        "assignment_id": None,
        "billing_ratio_percent": "100",
        "note": "11/10入場",
    }
    values.update(overrides)
    return StatusSwitchInput(**values)  # type: ignore[arg-type]


def test_tc_scn_015_switch_closes_open_period_and_adds_new_one(db_path: Path) -> None:
    assignment_id = _assignment(db_path)
    with open_connection(db_path) as connection:
        first = create_work_period(connection, 1, _period())
        new_id = switch_work_status(
            connection, 1, _switch(work_status_code="DEV", assignment_id=assignment_id)
        )

        periods = list_work_periods(connection, 1)
        logs = connection.execute(
            "SELECT operation, target_key, before_value, after_value FROM operation_log "
            "WHERE target_table = 'work_period' ORDER BY id"
        ).fetchall()

    assert [(period.id, period.start_date, period.end_date) for period in periods] == [
        (first, date(2026, 10, 1), date(2026, 11, 9)),
        (new_id, date(2026, 11, 10), None),
    ]
    assert (periods[1].work_status_code, periods[1].assignment_id) == ("DEV", assignment_id)
    assert [(log[0], log[1]) for log in logs[1:]] == [("更新", str(first)), ("登録", str(new_id))]
    assert json.loads(logs[1][2]) == {"end_date": None}
    assert json.loads(logs[1][3]) == {"end_date": "2026-11-09"}


def test_switch_without_open_period_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_work_period(connection, 1, _period(end_date=date(2026, 10, 31)))

        with pytest.raises(NoOpenPeriodError):
            switch_work_status(connection, 1, _switch())


@pytest.mark.parametrize("switch_date", [date(2026, 10, 1), date(2026, 9, 30)])
def test_switch_on_or_before_open_period_start_is_rejected(
    db_path: Path, switch_date: date
) -> None:
    with open_connection(db_path) as connection:
        create_work_period(connection, 1, _period())

        with pytest.raises(InvalidSwitchDateError):
            switch_work_status(connection, 1, _switch(switch_date=switch_date))


def test_switch_on_the_day_after_start_is_accepted(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_work_period(connection, 1, _period())
        switch_work_status(connection, 1, _switch(switch_date=date(2026, 10, 2)))

        assert list_work_periods(connection, 1)[0].end_date == date(2026, 10, 1)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"work_status_code": "DEV"}, MissingAssignmentError),
        ({"billing_ratio_percent": "abc"}, InvalidBillingRatioError),
    ],
)
def test_failed_switch_changes_nothing(
    db_path: Path, overrides: dict[str, object], error: type[Exception]
) -> None:
    with open_connection(db_path) as connection:
        create_work_period(connection, 1, _period())

        with pytest.raises(error):
            switch_work_status(connection, 1, _switch(**overrides))

        periods = list_work_periods(connection, 1)
        log_count = connection.execute(
            "SELECT COUNT(*) FROM operation_log WHERE target_table = 'work_period'"
        ).fetchone()[0]
    assert [(period.start_date, period.end_date) for period in periods] == [
        (date(2026, 10, 1), None)
    ]
    assert log_count == 1


def test_switch_rejects_other_employees_assignment(db_path: Path) -> None:
    other_assignment = _assignment(db_path, employee_id=2)
    with open_connection(db_path) as connection:
        create_work_period(connection, 1, _period())

        with pytest.raises(AssignmentNotOwnedError):
            switch_work_status(
                connection, 1, _switch(work_status_code="DEV", assignment_id=other_assignment)
            )


def test_switch_rejects_period_starting_after_open_period(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_work_period(connection, 1, _period())
        # 重なり禁止に反する異常なデータ（継続中の期間より後に始まる期間）を直接入れる。
        connection.execute(
            "INSERT INTO work_period (employee_id, start_date, end_date, work_status_code, "
            "billing_ratio_bp, created_at, updated_at) "
            "VALUES (1, '2026-12-01', '2026-12-31', 'WAIT_FULL', 10000, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

        with pytest.raises(PeriodOverlapError):
            switch_work_status(connection, 1, _switch())
        assert list_work_periods(connection, 1)[0].end_date is None
