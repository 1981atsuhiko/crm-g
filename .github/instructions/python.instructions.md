---
description: "Use when writing, editing, or reviewing Python code: version, type hints, dataclass, ruff, dependencies, packaging, exceptions, resources, paths, datetime, pytest and mock. Python 実装ルール。"
applyTo: "**/*.py"
---
# Python 実装ルール

共通のコーディング規約（[coding.instructions.md](./coding.instructions.md)）に加えて、Python 固有のルールを定める。

## 環境・依存管理
- Python のバージョンを固定して明記する（`pyproject.toml` の `requires-python`）。サポート期間内の版のみ使う。
- 依存は `pyproject.toml` で管理し、ロックファイルでバージョンを固定する。仮想環境を使う。
- 標準ライブラリ（`sqlite3`、`pathlib`、`logging`、`json`、`dataclasses` など）を優先し、外部ライブラリを増やさない。
- 設定値は設定ファイルから読み込む。`pydantic` などの検証ライブラリは、依存追加を確認してから導入する。

## 型・データ構造
- 全関数の引数・戻り値に型ヒントを付け、`Any` を使わない。
- `mypy`（または `pyright`）を strict で通す。
- 辞書やタプルを受け渡さず、`dataclass`（`frozen=True`）、`TypedDict`、`Enum` で型定義する。

## 静的解析・整形
- `ruff`（リント・フォーマット）を必須にし、警告ゼロを維持する。

## 安全性・堅牢性
- 可変オブジェクトをデフォルト引数にしない（`def f(x=[])` の禁止）。
- 裸の `except:` と `except Exception: pass` を使わない。独自例外を定義し、`raise ... from` で原因を保持する。
- ファイル・DB 接続は `with` で確実に解放する。
- `eval`、`exec`、`pickle`、`shell=True` の `subprocess` を使わない。
- パスは `pathlib.Path` で扱う。テキストモードの `open()` には必ず `encoding="utf-8"` を指定し、改行の扱い（`newline`）を明示する。バイナリモードでは `encoding` と `newline` を指定しない。
- 日時はタイムゾーン付きの `datetime` を使い、ナイーブな `datetime.now()` を使わない。

## 配布
- PyInstaller や Nuitka などでの単一実行ファイル化を前提に、ビルド手順と除外モジュールを管理する。
- GUI フレームワークは、サイズ・ライセンス・配布のしやすさを比較してから決める（TBD:）。

## テスト
- `pytest` と `unittest.mock`（または `pytest-mock`）を使う。
- テストにロジックを書かず、`parametrize` で固定値を与える。
