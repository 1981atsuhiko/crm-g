"""日時の保存形式（データ設計書 DBD-005）。

日時は、タイムゾーン付き（UTC）の datetime で扱い、DB には ISO 8601 形式
（UTC、末尾 Z）の文字列で保存する。ナイーブな datetime は扱わない
（Python実装ルール）。
"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """現在時刻を、タイムゾーン付き（UTC）で返す。"""
    return datetime.now(UTC)


def to_iso8601(value: datetime) -> str:
    """タイムゾーン付きの datetime を、ISO 8601 形式（UTC、末尾 Z）の文字列にする。

    マイクロ秒は常に6桁で出力する。桁数が変わると、DBのTEXT列での
    辞書式の比較（ORDER BY など）が、時系列の順序と一致しなくなるため。
    """
    if value.tzinfo is None:
        raise ValueError("value はタイムゾーン付きの datetime にしてください")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def from_iso8601(value: str) -> datetime:
    """ISO 8601 形式（末尾 Z）の文字列を、タイムゾーン付き（UTC）の datetime にする。"""
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("value はタイムゾーンの情報を含めてください")
    return parsed.astimezone(UTC)
