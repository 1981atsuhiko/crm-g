"""給与項目マスタの試験（`SCN-062`、`TBL-005`）。"""

from datetime import date
from pathlib import Path

import pytest

from crm_g.application.pay_item import (
    CodeNotFoundError,
    DuplicateCodeError,
    InvalidCategoryError,
    InvalidCodeError,
    InvalidDeductionOrderError,
    InvalidPeriodError,
    MutuallyExclusiveFlagsError,
    PayItemInput,
    create_pay_item,
    get_pay_item,
    list_pay_items,
    update_pay_item,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def _input(  # noqa: PLR0913 試験用のデータ生成関数のため、項目数が多くてもまとめて並べる
    *,
    name: str = "新手当",
    category: str = "固定支給",
    in_monthly_salary: bool = True,
    cost_direct: bool = False,
    deduction_order: int | None = None,
    sort_order: int = 99,
    valid_from: date = date(2026, 4, 1),
    valid_to: date | None = None,
) -> PayItemInput:
    return PayItemInput(
        name=name,
        category=category,
        in_monthly_salary=in_monthly_salary,
        cost_direct=cost_direct,
        deduction_order=deduction_order,
        sort_order=sort_order,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def test_list_pay_items_includes_seed_data(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        items = list_pay_items(connection)

        assert items[0].code == "BASE_SALARY"
        assert items[0].category == "基本給"


def test_get_pay_item_returns_none_when_not_found(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assert get_pay_item(connection, "NOT_EXIST") is None


def test_create_pay_item_succeeds(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_pay_item(connection, "NEW_ITEM", _input())

        item = get_pay_item(connection, "NEW_ITEM")
        assert item is not None
        assert item.name == "新手当"
        assert item.in_monthly_salary is True
        assert item.cost_direct is False


def test_create_pay_item_rejects_lowercase_code(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidCodeError):
        create_pay_item(connection, "new_item", _input())


def test_create_pay_item_rejects_duplicate_code(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(DuplicateCodeError):
        create_pay_item(connection, "BASE_SALARY", _input())


def test_create_pay_item_rejects_both_flags_on(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(MutuallyExclusiveFlagsError):
        create_pay_item(connection, "NEW_ITEM", _input(in_monthly_salary=True, cost_direct=True))


def test_create_pay_item_rejects_undefined_category(db_path: Path) -> None:
    """区分の選択肢はUI（st.selectbox）で絞られるため、アプリ層を直接呼んで確認する。"""
    with open_connection(db_path) as connection, pytest.raises(InvalidCategoryError):
        create_pay_item(connection, "NEW_ITEM", _input(category="未定義の区分"))


def test_create_pay_item_rejects_valid_to_before_valid_from(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidPeriodError):
        create_pay_item(
            connection,
            "NEW_ITEM",
            _input(valid_from=date(2026, 4, 1), valid_to=date(2026, 1, 1)),
        )


def test_create_pay_item_rejects_deduction_order_below_one(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidDeductionOrderError):
        create_pay_item(connection, "NEW_ITEM", _input(deduction_order=0))


def test_create_pay_item_rejects_duplicate_deduction_order(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_pay_item(connection, "ITEM_A", _input(deduction_order=50))

        with pytest.raises(InvalidDeductionOrderError):
            create_pay_item(connection, "ITEM_B", _input(deduction_order=50))


def test_update_pay_item_changes_fields(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        update_pay_item(
            connection,
            "HOUSING",
            _input(name="住宅手当（改）", category="固定支給", sort_order=4),
        )

        item = get_pay_item(connection, "HOUSING")
        assert item is not None
        assert item.name == "住宅手当（改）"


def test_update_pay_item_can_reuse_its_own_deduction_order(db_path: Path) -> None:
    """自分自身の減額順との重複は、エラーにならない（除外する）。"""
    with open_connection(db_path) as connection:
        item = get_pay_item(connection, "FIXED_OVERTIME")
        assert item is not None
        assert item.deduction_order == 7

        update_pay_item(
            connection,
            "FIXED_OVERTIME",
            _input(name=item.name, category=item.category, deduction_order=7),
        )

        updated = get_pay_item(connection, "FIXED_OVERTIME")
        assert updated is not None
        assert updated.deduction_order == 7


def test_update_pay_item_rejects_unknown_code(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(CodeNotFoundError):
        update_pay_item(connection, "NOT_EXIST", _input())


def test_create_pay_item_failure_rolls_back(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        with pytest.raises(MutuallyExclusiveFlagsError):
            create_pay_item(
                connection, "NEW_ITEM", _input(in_monthly_salary=True, cost_direct=True)
            )

        assert get_pay_item(connection, "NEW_ITEM") is None
