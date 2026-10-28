"""パスコードの設定・認証（SCD-006〜SCD-011）。

利用者を区別しない単一のパスコードで認証する（ロールなし）。ハッシュ方式は
scrypt とし、パラメータは行ごとに保存することで、将来パラメータを強くした
後も、古いハッシュを検証できるようにする（`SCD-007`）。
"""

import hashlib
import hmac
import secrets
import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from datetime import datetime, timedelta

from crm_g.application.operation_log import Operation, record_operation_log
from crm_g.domain.email_format import is_valid_email_format
from crm_g.infrastructure.datetime_utils import from_iso8601, to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

# PoC で確認済みの scrypt パラメータ（権限・セキュリティ設計書 3.1）。
_CURRENT_SCRYPT_N = 131072
_CURRENT_SCRYPT_R = 8
_CURRENT_SCRYPT_P = 1
_SCRYPT_MAXMEM = 256 * 1024 * 1024
_SALT_BYTES = 16
_HASH_BYTES = 32

_LOCKOUT_THRESHOLD = 5
_LOCKOUT_DURATION = timedelta(minutes=5)

_CurrentScryptParams = tuple[int, int, int]


class PasscodeMismatchError(RuntimeError):
    """確認用の入力が一致しないときのエラー。"""


class InvalidPasscodeFormatError(RuntimeError):
    """パスコードの文字種が、メールアドレス形式でないときのエラー。"""


class PasscodeNotSetError(RuntimeError):
    """パスコードが未設定のときのエラー。"""


class AccountLockedError(RuntimeError):
    """連続失敗によるロック中のときのエラー。"""


class InvalidPasscodeError(RuntimeError):
    """パスコードが正しくないときのエラー。"""


def _hash_passcode(passcode: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        passcode.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=_SCRYPT_MAXMEM,
        dklen=_HASH_BYTES,
    )


def is_passcode_set(connection: sqlite3.Connection) -> bool:
    """パスコードが設定済みかどうかを返す（`app_credential` に行があるか）。"""
    row = connection.execute("SELECT 1 FROM app_credential WHERE id = 1").fetchone()
    return row is not None


def _validate_new_passcode(passcode: str, confirm: str) -> None:
    """新しいパスコードの入力を検査する（確認用の入力との一致、メールアドレス形式）。

    一致しないときは `PasscodeMismatchError`、形式が正しくないときは
    `InvalidPasscodeFormatError` を送出する（`SCD-009`）。
    """
    if passcode != confirm:
        raise PasscodeMismatchError("確認用の入力が一致しません")
    if not is_valid_email_format(passcode):
        raise InvalidPasscodeFormatError("パスコードは、メールアドレス形式の文字列にしてください")


def setup_passcode(connection: sqlite3.Connection, passcode: str, confirm: str) -> None:
    """パスコードを設定する（初回設定・変更の共通処理）。

    確認用の入力と一致しないとき、メールアドレス形式でないときは、設定しない
    （`SCD-009`）。
    """
    _validate_new_passcode(passcode, confirm)

    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _hash_passcode(passcode, salt, _CURRENT_SCRYPT_N, _CURRENT_SCRYPT_R, _CURRENT_SCRYPT_P)
    now = to_iso8601(utc_now())

    with transaction(connection):
        if is_passcode_set(connection):
            connection.execute(
                "UPDATE app_credential SET algorithm = 'scrypt', scrypt_n = ?, scrypt_r = ?, "
                "scrypt_p = ?, salt = ?, hash = ?, failed_count = 0, locked_until = NULL, "
                "updated_at = ? WHERE id = 1",
                (
                    _CURRENT_SCRYPT_N,
                    _CURRENT_SCRYPT_R,
                    _CURRENT_SCRYPT_P,
                    salt.hex(),
                    digest.hex(),
                    now,
                ),
            )
        else:
            connection.execute(
                "INSERT INTO app_credential "
                "(id, algorithm, scrypt_n, scrypt_r, scrypt_p, salt, hash, failed_count, "
                "created_at, updated_at) "
                "VALUES (1, 'scrypt', ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    _CURRENT_SCRYPT_N,
                    _CURRENT_SCRYPT_R,
                    _CURRENT_SCRYPT_P,
                    salt.hex(),
                    digest.hex(),
                    now,
                    now,
                ),
            )
        record_operation_log(connection, Operation.PASSCODE_SET, target_table="app_credential")


def _apply_failure(connection: sqlite3.Connection, failed_count: int, now: datetime) -> None:
    new_failed_count = failed_count + 1
    new_locked_until = (
        to_iso8601(now + _LOCKOUT_DURATION) if new_failed_count >= _LOCKOUT_THRESHOLD else None
    )
    connection.execute(
        "UPDATE app_credential SET failed_count = ?, locked_until = ?, updated_at = ? WHERE id = 1",
        (new_failed_count, new_locked_until, to_iso8601(now)),
    )


def _apply_success(
    connection: sqlite3.Connection,
    passcode: str,
    current_params: _CurrentScryptParams,
    now: datetime,
) -> None:
    if current_params == (_CURRENT_SCRYPT_N, _CURRENT_SCRYPT_R, _CURRENT_SCRYPT_P):
        connection.execute(
            "UPDATE app_credential SET failed_count = 0, locked_until = NULL, updated_at = ? "
            "WHERE id = 1",
            (to_iso8601(now),),
        )
        return

    # 保存されているパラメータが古い場合は、現在のパラメータで再計算して保存する（SCD-007）。
    new_salt = secrets.token_bytes(_SALT_BYTES)
    new_digest = _hash_passcode(
        passcode, new_salt, _CURRENT_SCRYPT_N, _CURRENT_SCRYPT_R, _CURRENT_SCRYPT_P
    )
    connection.execute(
        "UPDATE app_credential SET scrypt_n = ?, scrypt_r = ?, scrypt_p = ?, salt = ?, hash = ?, "
        "failed_count = 0, locked_until = NULL, updated_at = ? WHERE id = 1",
        (
            _CURRENT_SCRYPT_N,
            _CURRENT_SCRYPT_R,
            _CURRENT_SCRYPT_P,
            new_salt.hex(),
            new_digest.hex(),
            to_iso8601(now),
        ),
    )


def verify_passcode(connection: sqlite3.Connection, passcode: str) -> None:
    """パスコードを検証する。

    正しければ、失敗回数をリセットし、ハッシュのパラメータが古い場合は、現在の
    パラメータで再計算して保存する（`SCD-007`）。正しくない場合・ロック中の場合は、
    例外を送出する（`SCD-008`、`SCD-010`）。
    """
    # 失敗回数の更新・操作ログの記録は、認証の成否にかかわらずコミットする必要がある
    # ため、例外は `transaction()` のブロックを抜けた後に送出する（ブロック内で送出
    # すると、ロールバックされてしまう）。
    with transaction(connection):
        row = connection.execute(
            "SELECT scrypt_n, scrypt_r, scrypt_p, salt, hash, failed_count, locked_until "
            "FROM app_credential WHERE id = 1"
        ).fetchone()
        if row is None:
            raise PasscodeNotSetError("パスコードが設定されていません")
        n, r, p, salt_hex, hash_hex, failed_count, locked_until = row

        now = utc_now()
        if locked_until is not None and from_iso8601(locked_until) > now:
            raise AccountLockedError("現在、入力を受け付けていません")

        candidate = _hash_passcode(passcode, bytes.fromhex(salt_hex), n, r, p)
        is_correct = hmac.compare_digest(candidate, bytes.fromhex(hash_hex))
        if is_correct:
            _apply_success(connection, passcode, (n, r, p), now)
            record_operation_log(connection, Operation.AUTH_SUCCESS, target_table="app_credential")
        else:
            _apply_failure(connection, failed_count, now)
            record_operation_log(connection, Operation.AUTH_FAILURE, target_table="app_credential")

    if not is_correct:
        raise InvalidPasscodeError("パスコードが正しくありません")


def change_passcode(
    connection: sqlite3.Connection, current_passcode: str, new_passcode: str, confirm: str
) -> None:
    """現在のパスコードを確認したうえで、新しいパスコードに変更する（`SCD-011`）。

    新しいパスコードの入力（確認用との一致、形式）を、現在のパスコードの確認より
    先に検査する。新しい入力の誤りで変更できないときに、現在のパスコードの確認
    （`認証成功`・`認証失敗` の記録、失敗回数の更新）を行わないためである。
    現在のパスコードが正しくないときは、`認証失敗` を記録して、変更しない（`SCD-008`）。
    """
    _validate_new_passcode(new_passcode, confirm)
    verify_passcode(connection, current_passcode)
    setup_passcode(connection, new_passcode, confirm)
