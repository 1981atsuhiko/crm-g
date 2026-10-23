"""メールアドレス形式の検査（DBD-001、SCD-009）。

社員のメールアドレス、およびパスコードの文字種（メールアドレス形式に限定。
権限・セキュリティ設計書 `SCD-009`）の、両方で使う共通の検査方法とする。
特定のドメインへの制限は設けない。
"""

import re

# ローカル部とドメイン部を @ で区切り、ドメイン部に . を1つ以上含む形式だけを
# 妥当とする。実在するアドレスであることまでは検査しない（形式の検査のみ）。
_EMAIL_FORMAT_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email_format(value: str) -> bool:
    """文字列が、メールアドレスとして妥当な形式かどうかを判定する。"""
    return bool(_EMAIL_FORMAT_PATTERN.fullmatch(value))
