"""データ取込の共通処理の試験（移行設計書 3章・7章、TC-MIG-001〜011）。

CSV は、試験の中でバイト列として組み立てる（.csv ファイルを置かない）。値はすべて架空である。
"""

from datetime import date

import pytest

from crm_g.application.import_common import (
    EXCLUDED_COLUMNS,
    ColumnLayout,
    ImportErrorCode,
    ImportIssue,
    ImportRow,
    InvalidCellError,
    issue_for_cell,
    match_choice,
    match_headers,
    normalize_cell,
    normalize_free_text,
    parse_amount,
    parse_date,
    parse_email,
    parse_half,
    parse_rate_bp,
    parse_year_month,
    read_import_csv,
    require_value,
)

_BOM = "\ufeff"
_LAYOUT = ColumnLayout(
    defined=("氏名", "メールアドレス", "年月", "売上", "勤務手当（職務給）"),
    required=("年月", "売上"),
    required_one_of=(("氏名", "メールアドレス"),),
)


def _csv(*lines: str, bom: bool = False, newline: str = "\r\n") -> bytes:
    """行の並びから、取込用 CSV のバイト列を作る。"""
    text = newline.join(lines) + newline
    return ((_BOM if bom else "") + text).encode()


def _code_of(error: pytest.ExceptionInfo[InvalidCellError]) -> ImportErrorCode:
    """送出されたエラーの種類を返す。"""
    return error.value.code


# ---------------------------------------------------------------------------
# エラーの種類
# ---------------------------------------------------------------------------


def test_error_codes_are_e001_to_e019_with_descriptions() -> None:
    """エラーの種類は E001〜E019 の19種類で、それぞれ説明を持つ。"""
    assert [code.value for code in ImportErrorCode] == [f"E{number:03d}" for number in range(1, 20)]
    assert all(code.description for code in ImportErrorCode)
    assert ImportErrorCode.NOT_UTF8.description == "文字コードが UTF-8 ではない"


def test_invalid_cell_error_message_has_only_code() -> None:
    """セルのエラーのメッセージは、エラーの種類だけを含む。"""
    error = InvalidCellError(ImportErrorCode.INVALID_FORMAT)

    assert error.code is ImportErrorCode.INVALID_FORMAT
    assert "E007" in str(error)


def test_issue_for_cell_adds_line_and_column() -> None:
    """セルのエラーに、行番号と列名を付ける。"""
    error = InvalidCellError(ImportErrorCode.OUT_OF_RANGE)

    assert issue_for_cell(3, "売上", error) == ImportIssue(3, "売上", ImportErrorCode.OUT_OF_RANGE)


# ---------------------------------------------------------------------------
# 読み込み（TC-MIG-001〜003）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bom", [True, False])
def test_tc_mig_001_002_reads_utf8_with_and_without_bom(bom: bool) -> None:
    """UTF-8 の BOM 付き・なしの両方を読める。"""
    content = _csv("氏名,年月,売上", "山田 太郎,2026/04,700000", bom=bom)

    result = read_import_csv(content, _LAYOUT)

    assert result.issues == ()
    assert result.rows == (
        ImportRow(2, {"氏名": "山田 太郎", "年月": "2026/04", "売上": "700000"}),
    )


def test_tc_mig_003_shift_jis_is_e001() -> None:
    """Shift_JIS は、ファイル全体の E001（行番号なし）。行を読まない。"""
    content = "氏名,年月,売上\r\n山田 太郎,2026/04,700000\r\n".encode("cp932")

    result = read_import_csv(content, _LAYOUT)

    assert result.issues == (ImportIssue(None, None, ImportErrorCode.NOT_UTF8),)
    assert result.rows == ()


@pytest.mark.parametrize(
    "content",
    [b"", _BOM.encode(), b"\r\n\r\n", b",,\r\n2026/04,1\r\n"],
)
def test_empty_file_or_blank_header_is_e002(content: bytes) -> None:
    """空のファイル・見出し行が空のときは、E002。"""
    result = read_import_csv(content, _LAYOUT)

    assert result.issues == (ImportIssue(None, None, ImportErrorCode.NO_HEADER),)


def test_broken_csv_is_e002() -> None:
    """NUL 文字を含む・引用符が閉じていないなど、CSV として読めないときは、E002。"""
    for content in (b"a,b\r\nc\x00,d\r\n", b'a,b\r\n"c,d\r\n'):
        result = read_import_csv(content, _LAYOUT)

        assert result.issues == (ImportIssue(None, None, ImportErrorCode.NO_HEADER),)


def test_cr_only_newlines_are_read() -> None:
    """改行が CR だけの CSV も読める。"""
    result = read_import_csv(_csv("氏名,年月,売上", "山田 太郎,2026/04,1", newline="\r"), _LAYOUT)

    assert result.issues == ()
    assert result.rows == (ImportRow(2, {"氏名": "山田 太郎", "年月": "2026/04", "売上": "1"}),)


def test_cell_larger_than_128kib_is_e002() -> None:
    """128KiB を超えるセルがあるときは、CSV として読めず、ファイル全体の E002。"""
    content = _csv("氏名,年月,売上", "x" * (128 * 1024 + 1) + ",2026/04,1")

    result = read_import_csv(content, _LAYOUT)

    assert result.issues == (ImportIssue(None, None, ImportErrorCode.NO_HEADER),)
    assert result.rows == ()


def test_csv_without_header_row_is_e002_without_values() -> None:
    """見出し行のない CSV（1行目がデータ）は、ファイル全体の E002。データの値をエラーに出さない。"""
    content = _csv("山田 太郎,2026/04,700000", "山田 太郎,2026/04,700000")

    result = read_import_csv(content, _LAYOUT)

    assert result.issues == (ImportIssue(None, None, ImportErrorCode.NO_HEADER),)
    assert result.rows == ()
    assert result.excluded_columns == ()
    assert "山田" not in repr(result)
    assert "700000" not in repr(result)


def test_header_with_no_matching_column_is_only_e002() -> None:
    """1列も一致しないときは、重複（E005）・定義にない列（E004）の列名を出さず、E002 だけにする。"""
    header = match_headers(["山田 太郎", "山田 太郎", "700000"], _LAYOUT)

    assert header.issues == (ImportIssue(None, None, ImportErrorCode.NO_HEADER),)
    assert header.positions == {}


def test_header_with_only_excluded_column_is_not_e002() -> None:
    """除外列だけが一致したときは、見出し行とみなす（必須の列がなければ E003）。"""
    header = match_headers(["パスワード", "単価"], _LAYOUT)

    assert header.issues == (
        ImportIssue(1, "単価", ImportErrorCode.UNKNOWN_COLUMN),
        ImportIssue(1, "年月", ImportErrorCode.MISSING_COLUMN),
        ImportIssue(1, "売上", ImportErrorCode.MISSING_COLUMN),
        ImportIssue(1, "氏名／メールアドレス", ImportErrorCode.MISSING_COLUMN),
    )


def test_blank_rows_are_skipped_but_counted_in_line_numbers() -> None:
    """空行は読み飛ばすが、行番号には数える。短い行は、足りないセルを空とする。"""
    content = _csv("氏名,年月,売上", "", " , ,\u3000", "山田 太郎,2026/04")

    result = read_import_csv(content, _LAYOUT)

    assert result.issues == ()
    assert result.rows == (ImportRow(4, {"氏名": "山田 太郎", "年月": "2026/04", "売上": ""}),)


def test_header_only_file_has_no_rows() -> None:
    """見出し行だけのファイルは、行なし・エラーなし。"""
    result = read_import_csv(_csv("氏名,年月,売上"), _LAYOUT)

    assert result.rows == ()
    assert result.issues == ()


def test_value_without_header_is_e004_on_the_row() -> None:
    """見出しのない位置（空の見出し・見出しより右）に値があるときは、その行の E004。"""
    content = _csv("氏名,,年月,売上", "山田 太郎,,2026/04,1", "山田 次郎,x,2026/04,1", "a,,b,c,d")

    result = read_import_csv(content, _LAYOUT)

    assert result.issues == (
        ImportIssue(3, None, ImportErrorCode.UNKNOWN_COLUMN),
        ImportIssue(4, None, ImportErrorCode.UNKNOWN_COLUMN),
    )
    assert len(result.rows) == 3


# ---------------------------------------------------------------------------
# 見出し（TC-MIG-004〜007）
# ---------------------------------------------------------------------------


def test_tc_mig_004_missing_required_column_is_e003_with_column_name() -> None:
    """必須の列がないときは、E003。列名が分かる。氏名・メールアドレスの両方がないときも E003。"""
    result = read_import_csv(_csv("年月", "2026/04"), _LAYOUT)

    assert result.issues == (
        ImportIssue(1, "売上", ImportErrorCode.MISSING_COLUMN),
        ImportIssue(1, "氏名／メールアドレス", ImportErrorCode.MISSING_COLUMN),
    )
    assert result.rows == ()


def test_either_name_or_email_column_is_enough() -> None:
    """氏名とメールアドレスは、どちらか一方の列があればよい。"""
    result = read_import_csv(_csv("メールアドレス,年月,売上"), _LAYOUT)

    assert result.issues == ()


def test_tc_mig_005_unknown_column_is_e004() -> None:
    """定義にない列があるときは、E004。"""
    result = read_import_csv(_csv("氏名,年月,売上,単価", "山田 太郎,2026/04,1,2"), _LAYOUT)

    assert result.issues == (ImportIssue(1, "単価", ImportErrorCode.UNKNOWN_COLUMN),)
    assert result.rows == ()


@pytest.mark.parametrize("excluded", EXCLUDED_COLUMNS)
def test_tc_mig_006_excluded_column_is_dropped_and_listed(excluded: str) -> None:
    """除外列は読み捨て、列名を「取り込まなかった列」として返す。値は行に残らない。"""
    content = _csv(f"氏名,{excluded},年月,売上", "山田 太郎,secret-value,2026/04,1")

    result = read_import_csv(content, _LAYOUT)

    assert result.issues == ()
    assert result.excluded_columns == (excluded.replace("（", "(").replace("）", ")"),)
    assert result.rows == (ImportRow(2, {"氏名": "山田 太郎", "年月": "2026/04", "売上": "1"}),)
    assert "secret-value" not in repr(result)


def test_tc_mig_007_duplicate_header_is_e005() -> None:
    """見出しが重複するときは、E005（正規化した後の重複を含む）。"""
    result = read_import_csv(_csv("氏名,年月,売上, 売上 ,ﾊﾟｽﾜｰﾄﾞ,パスワード"), _LAYOUT)

    assert result.issues == (
        ImportIssue(1, "売上", ImportErrorCode.DUPLICATE_COLUMN),
        ImportIssue(1, "パスワード", ImportErrorCode.DUPLICATE_COLUMN),
    )


def test_headers_are_matched_after_nfkc_and_strip() -> None:
    """見出しは、NFKC 正規化と前後の空白の除去をしてから照合する。列の順は問わない。"""
    header = match_headers(
        ["\u3000売上 ", "勤務手当(職務給)", "年月", "ｍｅｍｏ"],
        ColumnLayout(defined=("年月", "売上", "勤務手当（職務給）"), required=("年月",)),
    )

    assert header.positions == {"売上": 0, "勤務手当（職務給）": 1, "年月": 2}
    assert header.issues == (ImportIssue(1, "memo", ImportErrorCode.UNKNOWN_COLUMN),)
    assert header.excluded_columns == ()
    assert header.excluded_positions == frozenset()


@pytest.mark.parametrize("header", ["氏名\u200b", "\ufeff氏名", "\u200b 氏名\u2060 "])
def test_headers_are_matched_after_removing_format_characters(header: str) -> None:
    """見出しの書式文字（2つ目の BOM、ゼロ幅スペースなど）は、取り除いてから照合する。"""
    result = read_import_csv(_csv(f"{header},年月,売上", "山田 太郎,2026/04,1", bom=True), _LAYOUT)

    assert result.issues == ()
    assert result.rows == (ImportRow(2, {"氏名": "山田 太郎", "年月": "2026/04", "売上": "1"}),)


@pytest.mark.parametrize(
    ("defined", "excluded"),
    [
        (("年月", "備考", "備考"), ()),
        (("年月", "勤務手当（職務給）", "勤務手当(職務給)"), ()),
        (("年月", "備考\u200b", " 備考"), ()),
        (("年月", "支払額"), EXCLUDED_COLUMNS),
        (("年月",), ("メモ", "ﾒﾓ")),
    ],
)
def test_column_layout_rejects_duplicates_after_normalization(
    defined: tuple[str, ...], excluded: tuple[str, ...]
) -> None:
    """列の定義に、正規化すると同じになる列名があるときは ValueError。

    除外列との重なりも含む。メッセージには、列名を含めない。
    """
    with pytest.raises(ValueError, match="同じになる列名") as raised:
        ColumnLayout(defined=defined, excluded=excluded)

    assert "備考" not in str(raised.value)
    assert "支払額" not in str(raised.value)
    assert "メモ" not in str(raised.value)


def test_excluded_columns_can_be_overridden() -> None:
    """除外列の一覧は、列の定義ごとに変えられる。"""
    header = match_headers(["年月", "メモ"], ColumnLayout(defined=("年月",), excluded=("メモ",)))

    assert header.excluded_columns == ("メモ",)
    assert header.excluded_positions == frozenset({1})
    assert header.issues == ()


# ---------------------------------------------------------------------------
# 文字列の正規化（MIG-022）
# ---------------------------------------------------------------------------


def test_normalize_cell_applies_nfkc_and_strip() -> None:
    """区分値などのセルは、NFKC 正規化と前後の空白（全角を含む）の除去をする。空は None。"""
    assert normalize_cell("\u3000ＳＥＳ ") == "SES"
    assert normalize_cell("ﾃｽﾄ") == "テスト"
    assert normalize_cell(" \u3000") is None
    assert normalize_cell("") is None


def test_normalize_free_text_only_strips() -> None:
    """自由記述は、前後の空白の除去だけをする。NFKC 正規化はしない。空は None。"""
    assert normalize_free_text("\u3000ｻﾝﾌﾟﾙ商事（株） ") == "ｻﾝﾌﾟﾙ商事（株）"
    assert normalize_free_text("=SUM(A1)") == "=SUM(A1)"
    assert normalize_free_text("\t") is None


def test_require_value() -> None:
    """必須の値が空なら E006。"""
    assert require_value("x") == "x"
    with pytest.raises(InvalidCellError) as raised:
        require_value(None)
    assert _code_of(raised) is ImportErrorCode.REQUIRED_EMPTY


def test_match_choice() -> None:
    """区分値は、選択肢の表記を同じく正規化した値と完全一致で照合する。一致しなければ E008。"""
    choices = {"待機（減額無し）": "WAIT_FULL", "SES": "SES"}

    assert match_choice("待機(減額無し)", choices) == "WAIT_FULL"
    assert match_choice("SES", choices) == "SES"
    with pytest.raises(InvalidCellError) as raised:
        match_choice("ses", choices)
    assert _code_of(raised) is ImportErrorCode.NOT_IN_CHOICES


# ---------------------------------------------------------------------------
# 日付・年月・半期（TC-MIG-008、TC-MIG-009）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026/4/1", date(2026, 4, 1)),
        ("2026-04-01", date(2026, 4, 1)),
        ("2026/04/01", date(2026, 4, 1)),
        ("2024-2-29", date(2024, 2, 29)),
    ],
)
def test_tc_mig_008_parse_date_accepts_formats(text: str, expected: date) -> None:
    """日付は、`YYYY-MM-DD`・`YYYY/MM/DD`（月・日は1桁でもよい）を受け付ける。"""
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "2026/13/01",
        "2025-02-29",
        "0000-01-01",
        "2026/04-01",
        "20260401",
        "2026/4/1 0:00",
        "٢٠٢٦/4/1",
    ],
)
def test_tc_mig_008_parse_date_rejects_invalid_as_e007(text: str) -> None:
    """存在しない日付・その他の形式は E007。例外の原因に、値を含む ValueError を連結しない。"""
    with pytest.raises(InvalidCellError) as raised:
        parse_date(text)

    assert _code_of(raised) is ImportErrorCode.INVALID_FORMAT
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True or raised.value.__context__ is None


def test_parse_date_after_latest_is_e009() -> None:
    """`latest` より後の日付（未来の生年月日など）は E009。同じ日は受け付ける。"""
    today = date(2026, 10, 27)

    assert parse_date("2026/10/27", latest=today) == today
    with pytest.raises(InvalidCellError) as raised:
        parse_date("2026/10/28", latest=today)
    assert _code_of(raised) is ImportErrorCode.OUT_OF_RANGE


@pytest.mark.parametrize(
    ("text", "expected"), [("2026/4", "2026-04"), ("2026-04", "2026-04"), ("2027/12", "2027-12")]
)
def test_parse_year_month_accepts_formats(text: str, expected: str) -> None:
    """年月は、`YYYY-MM`・`YYYY/MM`（月は1桁でもよい）を受け付け、`YYYY-MM` にする。"""
    assert parse_year_month(text) == expected


@pytest.mark.parametrize("text", ["2026/13", "2026/0", "0000-01", "2026/04/01", "202604"])
def test_parse_year_month_rejects_invalid_as_e007(text: str) -> None:
    """存在しない月・その他の形式は E007。"""
    with pytest.raises(InvalidCellError) as raised:
        parse_year_month(text)
    assert _code_of(raised) is ImportErrorCode.INVALID_FORMAT


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2025-H1", "2025-H1"),
        ("19期上期", "2025-H1"),
        ("20期下期", "2026-H2"),
        ("2026-H2", "2026-H2"),
        ("1000-H1", "1000-H1"),
        ("9999-H2", "9999-H2"),
        ("1期上期", "2007-H1"),
        ("999期下期", "3005-H2"),
    ],
)
def test_tc_mig_009_parse_half_accepts_formats(text: str, expected: str) -> None:
    """半期は、`YYYY-H1`・`YYYY-H2`・`NN期上期`・`NN期下期` を受け付ける。"""
    assert parse_half(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "19期中期",
        "2025-H3",
        "2025-h1",
        "0000-H1",
        "0001-H1",
        "0999-H2",
        "12025-H1",
        "0期上期",
        "000期下期",
        "1000期上期",
        "19期",
        "上期",
    ],
)
def test_tc_mig_009_parse_half_rejects_invalid_as_e007(text: str) -> None:
    """範囲外（`YYYY` が 1000 未満、`NN期` が 0 期・4 桁以上）・その他の形式は E007。"""
    with pytest.raises(InvalidCellError) as raised:
        parse_half(text)
    assert _code_of(raised) is ImportErrorCode.INVALID_FORMAT


# ---------------------------------------------------------------------------
# 金額（TC-MIG-010）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1,000", 1000), ("1000", 1000), ("0", 0), ("1,234,567", 1234567), ("-0", 0)],
)
def test_tc_mig_010_parse_amount_accepts_integers(text: str, expected: int) -> None:
    """金額は、円単位の整数。桁区切りの `,` を受け付ける。"""
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    "text", ["1000.5", "¥1000", "1000円", "1,00", "1,0000", ",100", "+1000", "１０００ 円", "abc"]
)
def test_tc_mig_010_parse_amount_rejects_format_as_e007(text: str) -> None:
    """小数・通貨記号・単位・桁区切りの位置の誤りは E007（丸めない）。"""
    with pytest.raises(InvalidCellError) as raised:
        parse_amount(text)
    assert _code_of(raised) is ImportErrorCode.INVALID_FORMAT


@pytest.mark.parametrize("text", ["-1", "-1,000", "\u22121000", "\uff0d1,000", "\u22121,000"])
def test_tc_mig_010_parse_amount_negative_is_e009(text: str) -> None:
    """負の値（先頭のマイナス記号は `-`・U+2212・全角）は E009（下限 0）。"""
    with pytest.raises(InvalidCellError) as raised:
        parse_amount(text)
    assert _code_of(raised) is ImportErrorCode.OUT_OF_RANGE


@pytest.mark.parametrize(
    ("text", "expected"),
    [("999999999999999", 999_999_999_999_999), ("999,999,999,999,999", 999_999_999_999_999)],
)
def test_parse_amount_accepts_up_to_15_digits(text: str, expected: int) -> None:
    """金額は、数字（桁区切りを除く）15 桁まで受け付ける。"""
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "1000000000000000",
        "0000000000000000",
        "1,000,000,000,000,000",
        "99999999999999999999",
        "9" * 5000,
        "-" + "9" * 5000,
    ],
)
def test_parse_amount_over_15_digits_is_e009_without_exception(text: str) -> None:
    """15 桁を超える金額は E009。

    `int()` の桁数の上限（4300 桁）を超える値でも、例外で止まらない。
    """
    with pytest.raises(InvalidCellError) as raised:
        parse_amount(text)
    assert _code_of(raised) is ImportErrorCode.OUT_OF_RANGE


def test_parse_amount_with_custom_maximum() -> None:
    """上限は、列ごとに変えられる。上限を超える値は E009。"""
    assert parse_amount("100", maximum=100) == 100
    with pytest.raises(InvalidCellError) as raised:
        parse_amount("101", maximum=100)
    assert _code_of(raised) is ImportErrorCode.OUT_OF_RANGE


def test_parse_amount_with_custom_minimum() -> None:
    """下限は、列ごとに変えられる。"""
    assert parse_amount("-5", minimum=-10) == -5
    with pytest.raises(InvalidCellError) as raised:
        parse_amount("0", minimum=1)
    assert _code_of(raised) is ImportErrorCode.OUT_OF_RANGE


# ---------------------------------------------------------------------------
# 率（TC-MIG-011）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("70%", 7000),
        ("0.7", 7000),
        ("70.00%", 7000),
        ("1", 10000),
        ("1.0", 10000),
        ("100%", 10000),
        ("0", 0),
        ("0%", 0),
        ("0.1234", 1234),
        ("12.34%", 1234),
        ("-0", 0),
        ("\u22120%", 0),
        ("001", 10000),
        ("100.00%", 10000),
    ],
)
def test_tc_mig_011_parse_rate_accepts_values(text: str, expected: int) -> None:
    """率は、`%` 付き（小数第2位まで）・なし（小数第4位まで）を万分率にする。"""
    assert parse_rate_bp(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "70",
        "70.001%",
        "1.00001",
        "100.01%",
        "1.0001",
        "0.12345",
        "101%",
        "0001",
        "0100%",
        "9" * 5000,
        "0." + "1" * 5000,
        "1" * 5000 + "." + "1" * 5000 + "%",
        "-0.5",
        "\u22120.5",
        "\uff0d50%",
        "-1",
    ],
)
def test_tc_mig_011_parse_rate_out_of_range_is_e009(text: str) -> None:
    """1 を超える値（`%` なしの 70 を含む）・負数・桁数の超過（整数部 3 桁超、小数部が `%` 付き
    2 桁・なし 4 桁超）は E009（丸めない）。巨大な桁数の値でも、例外で止まらない。"""
    with pytest.raises(InvalidCellError) as raised:
        parse_rate_bp(text)
    assert _code_of(raised) is ImportErrorCode.OUT_OF_RANGE


@pytest.mark.parametrize("text", ["abc", ".7", "7.", "--0.5", "+0.5", "70 %", "%"])
def test_parse_rate_invalid_format_is_e007(text: str) -> None:
    """数値でない値は E007。"""
    with pytest.raises(InvalidCellError) as raised:
        parse_rate_bp(text)
    assert _code_of(raised) is ImportErrorCode.INVALID_FORMAT


# ---------------------------------------------------------------------------
# メールアドレス
# ---------------------------------------------------------------------------


def test_parse_email_lowercases() -> None:
    """メールアドレスは、小文字にする。"""
    assert parse_email("Taro.Yamada@Example.com") == "taro.yamada@example.com"


@pytest.mark.parametrize("text", ["taro", "a@b@c", "taro yamada@example.com"])
def test_parse_email_invalid_is_e007(text: str) -> None:
    """`@` がちょうど1つでない・空白を含むときは E007。"""
    with pytest.raises(InvalidCellError) as raised:
        parse_email(text)
    assert _code_of(raised) is ImportErrorCode.INVALID_FORMAT
