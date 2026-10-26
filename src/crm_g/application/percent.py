"""百分率の入力（0〜100、小数第2位まで）を、万分率（100.00%＝10000）に変換する。

給与支給率（`SCN-061`）と稼働率（`SCN-050`）で共通に使う。
"""

from decimal import Decimal, InvalidOperation

_MAX_PERCENT = Decimal("100")
_BP_PER_PERCENT = Decimal("100")


class InvalidPercentError(RuntimeError):
    """百分率の入力が、数値でない・範囲外・小数第3位以下を含むときのエラー。"""


def parse_percent_to_bp(text: str, field_name: str) -> int:
    """「〜（%）」の入力文字列を、万分率の整数に変換する。`field_name` はメッセージに使う。"""
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise InvalidPercentError(f"{field_name}は、数値で入力してください") from error

    # `Decimal("nan")` はここでは例外にならず、後続の比較で InvalidOperation を
    # 起こすため、ここで明示的に弾く。
    if value.is_nan():
        raise InvalidPercentError(f"{field_name}は、数値で入力してください")

    if not (0 <= value <= _MAX_PERCENT):
        raise InvalidPercentError(f"{field_name}は、0〜100の範囲で入力してください")

    scaled = value * _BP_PER_PERCENT
    if scaled != scaled.to_integral_value():
        raise InvalidPercentError(f"{field_name}は、小数第2位までで入力してください")

    return int(scaled)
