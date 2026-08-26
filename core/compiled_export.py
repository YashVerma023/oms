"""Compiled downloads for the Setup tab.

Two workbooks, both admin-only:

  * **Usersetting** - every account on every server on one sheet, with the
    server as a column. A review copy, deliberately not the platform's
    per-server upload format - use the Usersetting CSVs button for that.
  * **All Users** - one day's `all_users` rows on a 'Main' sheet, in the layout
    the upload expects, so a downloaded file can be edited and handed straight
    back to OMP.

Booleans and times are rendered by `usersetting_export._render`, so True/False
and 15:15:00 look the way the platform writes them. Numbers stay numbers: a
column of allocations that Excel cannot sum is no use to anyone.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text

from core import usersetting_export
from core.importer import IMPORT_SPECS
from database import schema
from database.db import db

logger = logging.getLogger(__name__)

# Written by the portal, not by the sheet, so they are left out of a file that
# is meant to be uploaded again.
ALL_USERS_SKIP = ("id", "Date", "created_at", "updated_at")


def _sheet_headers(spec_key: str, columns: list[str]) -> list[str]:
    """Table columns under the headers the upload expects.

    The importer renames a few on the way in (`ml_pct` <- '%'); reversing that
    here is what makes the download re-uploadable.
    """
    renames = IMPORT_SPECS[spec_key].renames
    return [renames.get(name, name) for name in columns]


def _cell(value: Any, name: str, booleans: set[str]) -> Any:
    """One cell, native where Excel can hold the type."""
    if value is None:
        return None
    if name in booleans or isinstance(value, bool):
        return usersetting_export._render(value, name, booleans)
    if isinstance(value, Decimal):
        # Every figure here is well inside float's exact-integer range.
        return float(value)
    if isinstance(value, dt.timedelta):          # a TIME column
        return usersetting_export._render(value, name, booleans)
    # str, int, float, date and datetime all go in as they are.
    return value


def _write(sheet_name: str, headers: list[str], rows: list[list[Any]]) -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = sheet_name
    sheet.append(headers)
    for row in rows:
        sheet.append(row)

    # The header row stays put while scrolling a few thousand accounts.
    sheet.freeze_panes = "A2"

    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Usersetting - every server on one sheet
# ---------------------------------------------------------------------------

def usersetting_filename(on_date: dt.date | None = None) -> str:
    """'USERSETTINGS COMPILED 21 AUG 26.xlsx'."""
    stamp = (on_date or dt.date.today()).strftime("%d %b %y").upper()
    return f"USERSETTINGS COMPILED {stamp}.xlsx"


def usersetting_workbook(
    servers: list[str] | None = None, on_date: dt.date | None = None
) -> tuple[str, bytes, int]:
    """Every account's settings on one sheet.

    Args:
        servers: None for every server, else the ones an operator may see.

    Returns:
        (filename, xlsx bytes, row count)

    Raises:
        ValueError: there is nothing to export.
    """
    columns = usersetting_export.export_columns()
    booleans = usersetting_export._boolean_columns()

    sql = f"SELECT `server`, {', '.join(f'`{c}`' for c in columns)} FROM `usersetting` "
    params: dict[str, Any] = {}
    binds = []
    if servers is not None:
        if not servers:
            raise ValueError("No servers are assigned to you, so there is nothing to export.")
        sql += "WHERE `server` IN :servers "
        params["servers"] = servers
        binds.append(bindparam("servers", expanding=True))
    sql += "ORDER BY `server`, `User ID`"

    statement = text(sql)
    if binds:
        statement = statement.bindparams(*binds)
    records = db.session.execute(statement, params).mappings().all()

    if not records:
        raise ValueError("No usersetting rows to export.")

    # `server` leads: it is what tells the two halves of a shared User ID apart.
    headers = ["server", *usersetting_export.header_row()]
    rows = [
        [str(record["server"] or "")]
        + [_cell(record.get(c), c, booleans) for c in columns]
        for record in records
    ]

    logger.info("Compiled usersetting export: %s row(s)", len(rows))
    return usersetting_filename(on_date), _write("Usersetting", headers, rows), len(rows)


# ---------------------------------------------------------------------------
# All Users - one day, in the upload layout
# ---------------------------------------------------------------------------

def all_users_filename(on_date: dt.date) -> str:
    """'ALL USERS 21 AUG 26.xlsx'."""
    return f"ALL USERS {on_date.strftime('%d %b %y').upper()}.xlsx"


def all_users_workbook(
    on_date: dt.date, servers: list[str] | None = None
) -> tuple[str, bytes, int]:
    """One day's all_users rows on a 'Main' sheet.

    The column order and headers match what the All Users upload reads, so the
    file can go straight back in. `Date` is not written: the upload form asks
    for it, and a stale column in the sheet would be ignored anyway.

    Raises:
        ValueError: that date has no rows.
    """
    columns = [
        name for name, _ in schema.ddl_columns("all_users")
        if name not in ALL_USERS_SKIP
    ]

    sql = f"SELECT {', '.join(f'`{c}`' for c in columns)} FROM `all_users` WHERE `Date` = :d "
    params: dict[str, Any] = {"d": on_date}
    binds = []
    if servers is not None:
        if not servers:
            raise ValueError("No servers are assigned to you, so there is nothing to export.")
        sql += "AND `server` IN :servers "
        params["servers"] = servers
        binds.append(bindparam("servers", expanding=True))
    sql += "ORDER BY `server`, `userId`"

    statement = text(sql)
    if binds:
        statement = statement.bindparams(*binds)
    records = db.session.execute(statement, params).mappings().all()

    if not records:
        raise ValueError(f"No All Users rows for {on_date}.")

    booleans: set[str] = set()
    rows = [
        [_cell(record.get(c), c, booleans) for c in columns]
        for record in records
    ]

    logger.info("Compiled All Users export for %s: %s row(s)", on_date, len(rows))
    return (
        all_users_filename(on_date),
        _write("Main", _sheet_headers("all-users", columns), rows),
        len(rows),
    )
