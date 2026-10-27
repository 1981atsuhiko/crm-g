"""パスコードの設定・認証の試験（TC-SCD-001〜006、008、009）。"""

import hashlib
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

import crm_g.application.auth as auth_module
from crm_g.application.auth import (
    AccountLockedError,
    InvalidPasscodeError,
    InvalidPasscodeFormatError,
    PasscodeMismatchError,
    PasscodeNotSetError,
    change_passcode,
    is_passcode_set,
    setup_passcode,
    verify_passcode,
)
from crm_g.infrastructure.datetime_utils import from_iso8601, to_iso8601, utc_now
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_PASSCODE = "taro@3sss.co.jp"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def test_tc_scd_001_passcode_not_set_initially(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assert is_passcode_set(connection) is False


def test_tc_scd_002_mismatched_confirmation_is_rejected(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        with pytest.raises(PasscodeMismatchError):
            setup_passcode(connection, _PASSCODE, "different@3sss.co.jp")

        assert is_passcode_set(connection) is False


def test_setup_rejects_non_email_format(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidPasscodeFormatError):
        setup_passcode(connection, "not-an-email", "not-an-email")


def test_tc_scd_003_correct_passcode_passes_and_logs_auth_success(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)
        verify_passcode(connection, _PASSCODE)

        row = connection.execute(
            "SELECT COUNT(*) FROM operation_log WHERE operation = '認証成功'"
        ).fetchone()
        assert row[0] == 1


def test_tc_scd_004_wrong_passcode_increments_failed_count_and_logs(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

        with pytest.raises(InvalidPasscodeError):
            verify_passcode(connection, "wrong@3sss.co.jp")

        failed_count = connection.execute(
            "SELECT failed_count FROM app_credential WHERE id = 1"
        ).fetchone()[0]
        assert failed_count == 1

        row = connection.execute(
            "SELECT COUNT(*) FROM operation_log WHERE operation = '認証失敗'"
        ).fetchone()
        assert row[0] == 1


def test_tc_scd_005_lockout_after_threshold_rejects_even_correct_passcode(
    db_path: Path,
) -> None:
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

        for _ in range(5):
            with pytest.raises(InvalidPasscodeError):
                verify_passcode(connection, "wrong@3sss.co.jp")

        locked_until = connection.execute(
            "SELECT locked_until FROM app_credential WHERE id = 1"
        ).fetchone()[0]
        assert locked_until is not None

        with pytest.raises(AccountLockedError):
            verify_passcode(connection, _PASSCODE)


def test_tc_scd_006_lockout_persists_across_reconnection(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)
        for _ in range(5):
            with pytest.raises(InvalidPasscodeError):
                verify_passcode(connection, "wrong@3sss.co.jp")

    # 再接続（アプリの再起動を模す）してもロックが続く。
    with open_connection(db_path) as connection, pytest.raises(AccountLockedError):
        verify_passcode(connection, _PASSCODE)


def test_tc_scd_008_app_credential_never_stores_raw_passcode(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

        row = connection.execute("SELECT salt, hash FROM app_credential WHERE id = 1").fetchone()
        assert _PASSCODE not in row[0]
        assert _PASSCODE not in row[1]


def test_tc_scd_009_param_upgrade_rehashes_after_successful_auth(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

        # 現在より弱いパラメータで計算したハッシュに書き換えて、「古いハッシュ」を模す。
        weak_n = 16384
        weak_salt = bytes.fromhex(
            connection.execute("SELECT salt FROM app_credential WHERE id = 1").fetchone()[0]
        )
        weak_hash = hashlib.scrypt(
            _PASSCODE.encode("utf-8"), salt=weak_salt, n=weak_n, r=8, p=1, maxmem=2**28, dklen=32
        )
        connection.execute(
            "UPDATE app_credential SET scrypt_n = ?, hash = ? WHERE id = 1",
            (weak_n, weak_hash.hex()),
        )
        connection.commit()

        old_hash = connection.execute("SELECT hash FROM app_credential WHERE id = 1").fetchone()[0]

        verify_passcode(connection, _PASSCODE)

        row = connection.execute(
            "SELECT scrypt_n, hash FROM app_credential WHERE id = 1"
        ).fetchone()
        assert row[0] == 131072
        assert row[1] != old_hash

        # 再ハッシュ後も、同じパスコードで検証できる。
        verify_passcode(connection, _PASSCODE)


def test_verify_passcode_raises_when_not_set(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(PasscodeNotSetError):
        verify_passcode(connection, _PASSCODE)


def test_change_passcode_requires_correct_current_passcode(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

        new_passcode = "jiro@3sss.co.jp"
        with pytest.raises(InvalidPasscodeError):
            change_passcode(connection, "wrong@3sss.co.jp", new_passcode, new_passcode)

        # 変更が失敗しても、元のパスコードで検証できる。
        verify_passcode(connection, _PASSCODE)


def test_change_passcode_leaves_credential_valid_when_new_passcode_is_invalid(
    db_path: Path,
) -> None:
    """現在のパスコードの確認は成功しても、新パスコードの確認が不一致なら変更しない。"""
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

        with pytest.raises(PasscodeMismatchError):
            change_passcode(connection, _PASSCODE, "jiro@3sss.co.jp", "saburo@3sss.co.jp")

        # 現在のパスコードのまま検証できる（変更されていない）。
        verify_passcode(connection, _PASSCODE)


def _operation_counts(connection: sqlite3.Connection) -> dict[str, int]:
    """操作ログの操作ごとの件数を返す。"""
    rows = connection.execute(
        "SELECT operation, COUNT(*) FROM operation_log GROUP BY operation"
    ).fetchall()
    return {operation: count for operation, count in rows}


def test_change_passcode_checks_new_passcode_before_verifying_current(db_path: Path) -> None:
    """新しいパスコードの不一致は、現在のパスコードの確認より先に検査する。

    現在のパスコードが誤っていても、新しい入力の誤りで止まり、`認証失敗` の記録も
    失敗回数の加算も行わない。
    """
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

        with pytest.raises(PasscodeMismatchError):
            change_passcode(connection, "wrong@3sss.co.jp", "jiro@3sss.co.jp", "saburo@3sss.co.jp")

        failed_count = connection.execute(
            "SELECT failed_count FROM app_credential WHERE id = 1"
        ).fetchone()[0]
        assert failed_count == 0
        assert _operation_counts(connection) == {"パスコード設定": 1}


def test_change_passcode_with_invalid_new_format_does_not_record_auth_success(
    db_path: Path,
) -> None:
    """新しいパスコードの形式の誤りでは、現在のパスコードを確認しない（`認証成功` を残さない）。"""
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

        with pytest.raises(InvalidPasscodeFormatError):
            change_passcode(connection, _PASSCODE, "not-an-email", "not-an-email")

        assert _operation_counts(connection) == {"パスコード設定": 1}
        verify_passcode(connection, _PASSCODE)


def test_change_passcode_with_wrong_current_records_auth_failure(db_path: Path) -> None:
    """現在のパスコードが誤っているときは、`認証失敗` を記録し、失敗回数を加算する（`SCD-008`）。"""
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

        new_passcode = "jiro@3sss.co.jp"
        with pytest.raises(InvalidPasscodeError):
            change_passcode(connection, "wrong@3sss.co.jp", new_passcode, new_passcode)

        failed_count = connection.execute(
            "SELECT failed_count FROM app_credential WHERE id = 1"
        ).fetchone()[0]
        assert failed_count == 1
        assert _operation_counts(connection) == {"パスコード設定": 1, "認証失敗": 1}


def test_change_passcode_succeeds_and_logs_passcode_set(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

        new_passcode = "jiro@3sss.co.jp"
        change_passcode(connection, _PASSCODE, new_passcode, new_passcode)

        with pytest.raises(InvalidPasscodeError):
            verify_passcode(connection, _PASSCODE)
        verify_passcode(connection, new_passcode)

        count = connection.execute(
            "SELECT COUNT(*) FROM operation_log WHERE operation = 'パスコード設定'"
        ).fetchone()[0]
        assert count == 2


def test_write_failure_during_verification_rolls_back_credential_and_log(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """操作ログの書き込みが失敗したとき、app_credentialの更新もロールバックされることを確認する

    （`SCD-021`。業務データの更新を含む同様の試験はPH-08以降でTC-SCD-010・011として行う）。
    """

    def _failing_record_operation_log(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("擬似的な書き込み失敗")

    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)
        updated_at_before = connection.execute(
            "SELECT updated_at FROM app_credential WHERE id = 1"
        ).fetchone()[0]

        monkeypatch.setattr(auth_module, "record_operation_log", _failing_record_operation_log)

        with pytest.raises(sqlite3.OperationalError):
            verify_passcode(connection, "wrong@3sss.co.jp")

        monkeypatch.undo()

        row = connection.execute(
            "SELECT failed_count, updated_at FROM app_credential WHERE id = 1"
        ).fetchone()
        assert row[0] == 0
        assert row[1] == updated_at_before

        log_count = connection.execute("SELECT COUNT(*) FROM operation_log").fetchone()[0]
        assert log_count == 1  # パスコード設定のログだけが残る


def test_lockout_duration_is_five_minutes(db_path: Path) -> None:
    """ロックの有効期限が、おおよそ5分後に設定されることを確認する。"""
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)
        before = utc_now()

        for _ in range(5):
            with pytest.raises(InvalidPasscodeError):
                verify_passcode(connection, "wrong@3sss.co.jp")

        locked_until = from_iso8601(
            connection.execute("SELECT locked_until FROM app_credential WHERE id = 1").fetchone()[0]
        )
        delta = (locked_until - before).total_seconds()
        assert 295 <= delta <= 305


def test_lockout_blocks_until_expiry_then_allows_again(db_path: Path) -> None:
    """ロックの解除時刻を過去に書き換え、その後は正しいパスコードで通過できることを確認する。

    scrypt の計算時間はマシンに依存するため、実時間の経過を待つ代わりに、
    `locked_until` を直接過去の時刻に書き換えて、期限切れの状態を再現する。
    """
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)
        for _ in range(5):
            with pytest.raises(InvalidPasscodeError):
                verify_passcode(connection, "wrong@3sss.co.jp")

        with pytest.raises(AccountLockedError):
            verify_passcode(connection, _PASSCODE)

        past = to_iso8601(utc_now() - timedelta(seconds=1))
        connection.execute("UPDATE app_credential SET locked_until = ? WHERE id = 1", (past,))
        connection.commit()

        verify_passcode(connection, _PASSCODE)
