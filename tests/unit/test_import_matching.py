"""データ取込の名寄せの試験（移行設計書 4章、TC-MIG-012〜016）。値はすべて架空である。

`TC-MIG-014`〜`TC-MIG-016` は、テスト計画書では IT である。ここでは、そのうちの名寄せの部分を
UT として確かめる（関数名の `_matching_part`）。CSV の取込を通した IT は、PH-15-3 で行う。
"""

import pytest

from crm_g.application.import_common import ImportErrorCode, ImportIssue
from crm_g.application.import_matching import (
    EMAIL_COLUMN,
    NAME_COLUMN,
    EmployeeCandidate,
    EmployeeMatch,
    UnmatchedPerson,
    build_employee_directory,
    match_employee,
    unmatched_people,
)

_DIRECTORY = build_employee_directory(
    [
        EmployeeCandidate(1, "山田 太郎", "taro.yamada@example.com"),
        EmployeeCandidate(2, "ヤマダ ハナコ", "hanako@example.com"),
        EmployeeCandidate(3, "John Smith", "john@example.com"),
        EmployeeCandidate(4, "佐藤 一郎", "ichiro.a@example.com"),
        EmployeeCandidate(5, "佐藤\u3000一郎", "Ichiro.B@Example.com"),
        EmployeeCandidate(6, "高橋 次郎", "jiro@example.com"),
    ]
)


@pytest.mark.parametrize("name", ["山田\u3000太郎", "山田 太郎", "山田\t太郎", " 山田太郎 "])
def test_tc_mig_012_names_with_any_whitespace_match_same_employee(name: str) -> None:
    """姓名の間が全角空白・半角空白・タブでも、同じ社員に一致する。"""
    match = match_employee(_DIRECTORY, 2, name, None)

    assert match.employee_id == 1
    assert match.issue is None


def test_tc_mig_013_half_width_kana_and_full_width_alphabet_match() -> None:
    """半角カナ・全角英字の氏名が、全角カナ・半角英字の氏名に一致する。"""
    assert match_employee(_DIRECTORY, 2, "ﾔﾏﾀﾞ ﾊﾅｺ", None).employee_id == 2
    assert match_employee(_DIRECTORY, 3, "ＪＯＨＮ ＳＭＩＴＨ", None).employee_id == 3


def test_tc_mig_014_matching_part_variant_kanji_is_e010() -> None:
    """異体字の氏名は一致せず、E010。照合に使った氏名を持つ。

    `TC-MIG-014` の名寄せの部分（UT）。取込の IT は PH-15-3 で行う。
    """
    match = match_employee(_DIRECTORY, 2, "髙橋 次郎", "")

    assert match == EmployeeMatch(
        2, None, ImportIssue(2, NAME_COLUMN, ImportErrorCode.EMPLOYEE_NOT_FOUND), "髙橋 次郎", None
    )


def test_tc_mig_015_matching_part_same_name_is_e011() -> None:
    """同姓同名の社員が2人いて、氏名だけの行は E011。

    `TC-MIG-015` の名寄せの部分（UT）。取込の IT は PH-15-3 で行う。
    """
    match = match_employee(_DIRECTORY, 5, "佐藤 一郎", None)

    assert match.employee_id is None
    assert match.issue == ImportIssue(5, NAME_COLUMN, ImportErrorCode.EMPLOYEE_AMBIGUOUS)


def test_tc_mig_016_matching_part_email_takes_priority_over_name() -> None:
    """メールアドレスの値がある行は、メールアドレスで特定する（大文字小文字・前後の空白を問わない）。

    `TC-MIG-016` の名寄せの部分（UT）。取込の IT は PH-15-3 で行う。
    """
    match = match_employee(_DIRECTORY, 2, "佐藤 一郎", " ICHIRO.B@example.com ")

    assert match.employee_id == 5
    assert match.email == "ICHIRO.B@example.com"


def test_email_not_found_is_e010_without_falling_back_to_name() -> None:
    """メールアドレスが一致しないときは、氏名が一致しても E010（氏名は使わない）。"""
    for email in ("nobody@example.com", "not-an-email"):
        match = match_employee(_DIRECTORY, 4, "山田 太郎", email)

        assert match.issue == ImportIssue(4, EMAIL_COLUMN, ImportErrorCode.EMPLOYEE_NOT_FOUND)


def test_full_width_email_is_normalized() -> None:
    """メールアドレスは、NFKC 正規化してから照合する。"""
    assert match_employee(_DIRECTORY, 2, None, "ｊｏｈｎ＠ｅｘａｍｐｌｅ．ｃｏｍ").employee_id == 3


def test_both_empty_is_e006() -> None:
    """氏名とメールアドレスが両方とも空のときは E006。列名は、CSV にある列を指す。"""
    assert match_employee(_DIRECTORY, 2, " ", "").issue == ImportIssue(
        2, NAME_COLUMN, ImportErrorCode.REQUIRED_EMPTY
    )
    assert match_employee(_DIRECTORY, 3, None, "").issue == ImportIssue(
        3, EMAIL_COLUMN, ImportErrorCode.REQUIRED_EMPTY
    )


@pytest.mark.parametrize(
    ("name_cell", "email_cell", "column"),
    [
        ("\u200b", "\u200b", NAME_COLUMN),
        ("\u3000\u200b\u2060", None, NAME_COLUMN),
        (None, "\u200b ", EMAIL_COLUMN),
        ("\u200b", "", NAME_COLUMN),
    ],
)
def test_format_characters_only_is_treated_as_empty(
    name_cell: str | None, email_cell: str | None, column: str
) -> None:
    """書式文字（ゼロ幅スペースなど）と空白だけの氏名・メールアドレスは空とし、両方空なら E006。"""
    match = match_employee(_DIRECTORY, 2, name_cell, email_cell)

    assert match == EmployeeMatch(
        2, None, ImportIssue(2, column, ImportErrorCode.REQUIRED_EMPTY), None, None
    )


def test_format_characters_only_email_falls_back_to_name() -> None:
    """書式文字だけのメールアドレスは空として扱い、氏名で照合する。"""
    match = match_employee(_DIRECTORY, 2, "山田 太郎", "\u200b")

    assert match.employee_id == 1
    assert match.email is None


def test_unmatched_people_groups_by_name_and_reason() -> None:
    """E010・E011 の行を、氏名・原因ごとにまとめる。一致した行・E006 の行は含めない。"""
    matches = [
        match_employee(_DIRECTORY, 2, "髙橋 次郎", None),
        match_employee(_DIRECTORY, 3, "山田 太郎", None),
        match_employee(_DIRECTORY, 4, "佐藤 一郎", None),
        match_employee(_DIRECTORY, 5, "髙橋 次郎", None),
        match_employee(_DIRECTORY, 6, None, None),
        match_employee(_DIRECTORY, 7, None, "nobody@example.com"),
    ]

    assert unmatched_people(matches) == (
        UnmatchedPerson("髙橋 次郎", None, (2, 5), ImportErrorCode.EMPLOYEE_NOT_FOUND),
        UnmatchedPerson("佐藤 一郎", None, (4,), ImportErrorCode.EMPLOYEE_AMBIGUOUS),
        UnmatchedPerson(None, "nobody@example.com", (7,), ImportErrorCode.EMPLOYEE_NOT_FOUND),
    )
