"""Usersetting download: one CSV per server, in the upload format.

The file the trading platform reads has six comment lines, then a header row,
then one row per account. Everything the portal added afterwards - `server`,
`algo`, the timestamps - is stripped, so a downloaded file can be handed
straight back to the platform or re-uploaded here.

Each file holds *every* account on that server, not only the ones the Setup
tab changed: it is a complete server config, not a diff.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
import zipfile
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text

from database import schema
from database.db import db

logger = logging.getLogger(__name__)

# Reproduced verbatim from the platform's own template - the loader on the
# other side skips them, but the file is not recognised without them.
PREAMBLE = (
    "# Please fill all values carefully. ANY VALUE WHICH IS NOT REQUIRED CAN BE LEFT BLANK.",
    "# For Boolean, True / False OR Yes / No can be used.",
    "# For NRML SqOff, 0 = None, 1 = All, 2 = Today",
    "# For Time, enter like 15:15:00.",
    "# Password & PIN: These are only required if you have selected for Auto Login. "
    "Auto login internally fills user details in browser for easy login. "
    "It is totally optional feature.",
    "# Broker: Zerodha, AliceBlue etc.",
)

# Columns the portal added. Everything before the first of these is what the
# platform sent us, in its original order.
ADDED_COLUMNS = ("server", "algo", "created_at", "updated_at")

# The platform writes this one header with a leading space. MySQL will not keep
# it, so it is put back on the way out and the file stays byte-identical to
# theirs. `test_usersetting_export` fails if the two ever drift apart.
HEADER_LABELS = {"LIMIT Type": " LIMIT Type"}

# Stored as BOOLEAN (0/1) but written back as the platform wrote them.
_BOOLEAN_TYPES = ("boolean", "bool")

_TRUE_TOKENS = {"1", "true", "yes", "y", "t"}


def export_columns() -> list[str]:
    """The upload columns, in file order, taken from the table definition."""
    out = []
    for name, _definition in schema.ddl_columns("usersetting"):
        if name in ADDED_COLUMNS:
            break
        out.append(name)
    return out


def header_row() -> list[str]:
    """The header line exactly as the platform writes it."""
    return [HEADER_LABELS.get(c, c) for c in export_columns()]


def _boolean_columns() -> set[str]:
    return {
        name
        for name, definition in schema.ddl_columns("usersetting")
        if definition.split()[0].lower() in _BOOLEAN_TYPES
    }


def _render(value: Any, name: str, booleans: set[str]) -> str:
    """One cell, as the platform writes it."""
    if value is None:
        return ""

    if name in booleans or isinstance(value, bool):
        # MySQL hands BOOLEAN back as 0/1, but the column has held plain text
        # in the past; the file always wants True/False.
        if isinstance(value, str):
            token = value.strip().lower()
            if not token:
                return ""
            return "True" if token in _TRUE_TOKENS else "False"
        return "True" if value else "False"

    if isinstance(value, Decimal):
        # 0.00 -> '0', 1.50 -> '1.5'. The platform writes plain numbers, and a
        # DECIMAL column would otherwise export padding it never sent us.
        normalised = value.normalize()
        return f"{normalised:f}"

    if isinstance(value, dt.timedelta):
        # A TIME column arrives as a timedelta.
        seconds = int(value.total_seconds())
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"

    if isinstance(value, (dt.time, dt.datetime, dt.date)):
        return value.isoformat()

    return str(value)


def filename(server: str, on_date: dt.date | None = None) -> str:
    """'VS3 21 AUG 26 USERSETTINGS.csv'."""
    on_date = on_date or dt.date.today()
    stamp = on_date.strftime("%d %b %y").upper()
    return f"{str(server).strip().upper()} {stamp} USERSETTINGS.csv"


def _rows(servers: list[str] | None) -> list[dict[str, Any]]:
    columns = ", ".join(f"`{c}`" for c in export_columns())
    sql = f"SELECT `server`, {columns} FROM `usersetting` "
    params: dict[str, Any] = {}
    binds = []

    if servers is not None:
        if not servers:
            return []
        sql += "WHERE `server` IN :servers "
        params["servers"] = servers
        binds.append(bindparam("servers", expanding=True))

    sql += "ORDER BY `server`, `User ID`"
    statement = text(sql)
    if binds:
        statement = statement.bindparams(*binds)
    return [dict(r) for r in db.session.execute(statement, params).mappings().all()]


def _csv(rows: list[dict[str, Any]]) -> str:
    """One server's rows as the platform's CSV."""
    columns = export_columns()
    booleans = _boolean_columns()

    buffer = io.StringIO()
    # The platform's own files use CRLF; keep that so a diff against one of
    # their exports is empty rather than every line.
    writer = csv.writer(buffer, lineterminator="\r\n")
    for line in PREAMBLE:
        buffer.write(line + "\r\n")
    writer.writerow(header_row())
    for row in rows:
        writer.writerow([_render(row.get(c), c, booleans) for c in columns])
    return buffer.getvalue()


def build(
    servers: list[str] | None = None, on_date: dt.date | None = None
) -> tuple[list[tuple[str, str]], list[str]]:
    """Per-server CSVs.

    Args:
        servers: None for every server, else the ones to include.
        on_date: the date in the file name; today by default.

    Returns:
        ([(filename, csv text)], user ids skipped for having no server)
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    orphans: list[str] = []

    for row in _rows(servers):
        server = str(row.pop("server", "") or "").strip()
        if not server:
            # No server means no file name. Reported rather than dropped.
            orphans.append(str(row.get("User ID") or "?"))
            continue
        grouped.setdefault(server.upper(), []).append(row)

    files = [
        (filename(server, on_date), _csv(rows))
        for server, rows in sorted(grouped.items())
    ]

    logger.info(
        "Usersetting export: %d file(s), %d account(s), %d without a server.",
        len(files), sum(len(r) for r in grouped.values()), len(orphans),
    )
    return files, orphans


def zipped(files: list[tuple[str, str]]) -> bytes:
    """The per-server CSVs in one archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files:
            archive.writestr(name, body)
    return buffer.getvalue()


def archive_name(on_date: dt.date | None = None) -> str:
    on_date = on_date or dt.date.today()
    return f"USERSETTINGS {on_date.strftime('%d %b %y').upper()}.zip"
