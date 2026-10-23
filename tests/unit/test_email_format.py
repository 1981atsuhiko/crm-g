"""メールアドレス形式の検査の試験（DBD-001、SCD-009）。"""

import pytest

from crm_g.domain.email_format import is_valid_email_format


@pytest.mark.parametrize(
    "value",
    [
        "taro@3sss.co.jp",
        "a@b.c",
        "taro.yamada+tag@example.co.jp",
    ],
)
def test_accepts_valid_email_format(value: str) -> None:
    assert is_valid_email_format(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "taro",
        "taro@",
        "@3sss.co.jp",
        "taro@3sss",
        "taro 3sss@example.com",
        "taro@example.com extra",
    ],
)
def test_rejects_invalid_email_format(value: str) -> None:
    assert is_valid_email_format(value) is False
