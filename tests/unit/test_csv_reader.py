"""CSV の読み込みの試験（移行設計書 `MIG-020`、TC-MIG-001〜003）。

CSV は、試験の中でバイト列として組み立てる。`.gitattributes` が改行を変えるため、
.csv ファイルを置かない。
"""

import pytest

from crm_g.infrastructure.csv_reader import CsvReadError, CsvReadFailure, CsvRecord, read_csv

_BOM = "\ufeff"


def test_tc_mig_001_reads_utf8_with_bom() -> None:
    """BOM 付きの UTF-8 を読める。BOM は見出しに残らない。"""
    content = (_BOM + "氏名,年月\r\n山田 太郎,2026/04\r\n").encode()

    assert read_csv(content) == (
        CsvRecord(1, ("氏名", "年月")),
        CsvRecord(2, ("山田 太郎", "2026/04")),
    )


def test_tc_mig_002_reads_utf8_without_bom_and_lf() -> None:
    """BOM なしの UTF-8、改行 LF も読める。"""
    content = "氏名,年月\n山田 太郎,2026/04\n".encode()

    assert read_csv(content) == (
        CsvRecord(1, ("氏名", "年月")),
        CsvRecord(2, ("山田 太郎", "2026/04")),
    )


def test_tc_mig_003_shift_jis_is_not_utf8() -> None:
    """Shift_JIS は、復号できずに NOT_UTF8 になる。メッセージに値を含まない。"""
    content = "氏名\r\n山田 太郎\r\n".encode("cp932")

    with pytest.raises(CsvReadError) as raised:
        read_csv(content)

    assert raised.value.failure is CsvReadFailure.NOT_UTF8
    assert raised.value.__cause__ is None
    assert "山田" not in str(raised.value)


def test_line_numbers_count_blank_lines_and_records() -> None:
    """行番号は、見出し行を 1 とするレコードの順番。空行も数え、引用符内の改行は数えない。"""
    content = b'a,b\r\n\r\n"x\r\ny",z\r\nc,d\r\n'

    assert read_csv(content) == (
        CsvRecord(1, ("a", "b")),
        CsvRecord(2, ()),
        CsvRecord(3, ("x\r\ny", "z")),
        CsvRecord(4, ("c", "d")),
    )


def test_empty_content_returns_no_records() -> None:
    """空のファイル（BOM だけを含む）は、レコードなし。"""
    assert read_csv(b"") == ()
    assert read_csv(_BOM.encode()) == ()


@pytest.mark.parametrize(
    "content",
    [
        b"a,b\r\nc\x00d,e\r\n",
        b'a,b\r\n"c,d\r\n',
        b'a,b\r\n"c"d,e\r\n',
    ],
)
def test_broken_csv_is_malformed(content: bytes) -> None:
    """NUL 文字・閉じていない引用符・引用符の後の文字は、MALFORMED になる。"""
    with pytest.raises(CsvReadError) as raised:
        read_csv(content)

    assert raised.value.failure is CsvReadFailure.MALFORMED
    assert raised.value.__cause__ is None
