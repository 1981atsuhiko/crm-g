"""DB接続・トランザクション補助（ARC-005）。

接続の生成・破棄は、インフラ層のこの1か所だけが行う。接続ごとに外部キー制約を
有効にする（SQLite は既定で無効のため）。更新は、1ユースケース1トランザクション
とし、途中で失敗したらロールバックする。
"""

import sqlite3  # noqa: TID251 インフラ層のDB接続はここに閉じ込める
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_MIN_SQLITE_VERSION = (3, 38, 0)


class UnsupportedSqliteVersionError(RuntimeError):
    """同梱・利用の SQLite が、必要な最小版（3.38.0）未満のときのエラー。"""


def _check_sqlite_version() -> None:
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
        required = ".".join(str(part) for part in _MIN_SQLITE_VERSION)
        raise UnsupportedSqliteVersionError(
            f"SQLite {required} 以上が必要です（現在: {sqlite3.sqlite_version}）"
        )


@contextmanager
def open_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """DBファイルへの接続を開く。外部キー制約を有効にし、使用後に確実に閉じる。"""
    _check_sqlite_version()

    connection = sqlite3.connect(db_path)
    try:
        # レガシーの暗黙トランザクション管理を無効にする。BEGIN IMMEDIATE・commit・
        # rollback を transaction() だけで明示的に行うため（公式ドキュメントの推奨）。
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
    finally:
        connection.close()


def open_read_only(db_path: Path) -> sqlite3.Connection:
    """DBファイルを読み取り専用で開く。ファイルを作らず、書き換えもしない。

    バックアップのファイルの検査・復元の読み出しに使う（画面設計書 `SCN-065`）。
    `mode=ro` の URI で開くため、存在しないファイルは作らずにエラーにする。
    使い終わったら、呼び出し側が必ず `close()` する。
    """
    _check_sqlite_version()
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """1ユースケース1トランザクションの単位を表す。

    タブ間の更新競合を防ぐため、`BEGIN IMMEDIATE` で排他的に開始する（ARC-007）。
    ブロック内で例外が起きたら、ロールバックしてから再送出する。

    すでにトランザクションの中で呼ばれたときは、新しく開始せず、外側のトランザクションに
    参加する（コミット・ロールバックは外側が行う）。更新ユースケースと再計算を、同じ
    トランザクションで行うため（`ARC-009`）。
    """
    if connection.in_transaction:
        yield connection
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        # commit() 自体が失敗した場合（ディスクI/Oエラーなど）は、ロールバックを
        # 試みずに例外を伝える。接続は open_connection 側で確実に閉じられる。
        connection.commit()
