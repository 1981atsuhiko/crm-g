"""POC-01：exe の中で、同梱の SQL・sqlite3・jpholiday・ライセンス一覧が使えることの確認。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from importlib import metadata
from pathlib import Path

import jpholiday

from crm_poc import paths

SQLITE_MEMORY_DATABASE = ":memory:"
SELECT_ITEM_NAME_SQL = "SELECT item_name FROM poc_item WHERE item_id = 1"
HOLIDAY_DATE = date(2026, 1, 1)
NON_HOLIDAY_DATE = date(2026, 1, 2)
UNKNOWN_LICENSE = "UNKNOWN"
STREAMLIT_DISTRIBUTION_NAME = "streamlit"


@dataclass(frozen=True)
class LicenseEntry:
    name: str
    version: str
    license_name: str


@dataclass(frozen=True)
class ProbeResult:
    frozen: bool
    sql_item: str
    holiday_new_year: str | None
    holiday_next_day: str | None
    license_count: int
    streamlit_license: str | None


def read_bundled_sql_item(schema_path: Path | None = None) -> str:
    sql_path = schema_path
    if sql_path is None:
        sql_path = paths.schema_path()
    script = sql_path.read_text(encoding="utf-8")
    with closing(sqlite3.connect(SQLITE_MEMORY_DATABASE)) as connection:
        connection.executescript(script)
        row = connection.execute(SELECT_ITEM_NAME_SQL).fetchone()
    return str(row[0])


def holiday_name(target: date) -> str | None:
    return jpholiday.is_holiday_name(target)


def list_licenses() -> list[LicenseEntry]:
    entries: list[LicenseEntry] = []
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name is None:
            continue
        license_name = distribution.metadata.get("License-Expression")
        if license_name is None:
            license_name = distribution.metadata.get("License")
        if license_name is None:
            license_name = UNKNOWN_LICENSE
        entries.append(LicenseEntry(name, distribution.version, license_name))
    return sorted(entries, key=lambda entry: entry.name.lower())


def find_license(entries: list[LicenseEntry], distribution_name: str) -> str | None:
    for entry in entries:
        if entry.name.lower() == distribution_name:
            return entry.license_name
    return None


def run_all_checks() -> ProbeResult:
    entries = list_licenses()
    return ProbeResult(
        frozen=paths.is_frozen(),
        sql_item=read_bundled_sql_item(),
        holiday_new_year=holiday_name(HOLIDAY_DATE),
        holiday_next_day=holiday_name(NON_HOLIDAY_DATE),
        license_count=len(entries),
        streamlit_license=find_license(entries, STREAMLIT_DISTRIBUTION_NAME),
    )
