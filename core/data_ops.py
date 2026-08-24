"""Data Operation: create a table, by hand or from a sheet.

Two flows, both ending in the same place:

  * a name plus a list of columns, typed in; or
  * an uploaded CSV/Excel whose headers and types are guessed, shown for
    editing, then used.

The DDL here is the only place in the portal that builds SQL identifiers from
user input, so every name is validated against `IDENTIFIER` before it goes
near a statement, and types are chosen from `TYPES` by key rather than passed
through. Values are always bound as parameters.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import re
from decimal import Decimal
from typing import Any, BinaryIO

from sqlalchemy import text

from core import importer
from database import schema
from database.db import db, get_config

logger = logging.getLogger(__name__)

# Letters, digits and underscore, not starting with a digit. Anything else is
# rejected rather than quoted, so no identifier can carry SQL out of here.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Trailing clause on CREATE TABLE. A constant so tests can run the same DDL
# against SQLite, which has no storage engines.
TABLE_OPTIONS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"

MAX_COLUMNS = 200
MAX_NAME = 64
SAMPLE_ROWS = 200          # rows read to guess a type
CHUNK = 500

# Offered types. The key is what the browser sends; nothing else is accepted.
TYPES: dict[str, dict[str, str]] = {
    "text": {"label": "Text (255)", "sql": "VARCHAR(255)"},
    "longtext": {"label": "Long text", "sql": "TEXT"},
    "int": {"label": "Whole number", "sql": "BIGINT"},
    "decimal": {"label": "Decimal", "sql": "DECIMAL(18,4)"},
    "date": {"label": "Date", "sql": "DATE"},
    "datetime": {"label": "Date and time", "sql": "DATETIME"},
    "bool": {"label": "Yes / No", "sql": "BOOLEAN"},
}

# A key column cannot be NULL, and TEXT cannot be indexed without a prefix
# length - a key column asking for TEXT is stored as VARCHAR instead.
KEY_SAFE_TYPE = {"longtext": "text"}


class DataOpError(ValueError):
    """Something the user can fix, safe to show them."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def check_identifier(name: str, what: str) -> str:
    name = (name or "").strip()
    if not name:
        raise DataOpError(f"{what} is required.")
    if len(name) > MAX_NAME:
        raise DataOpError(f"{what} '{name}' is longer than {MAX_NAME} characters.")
    if not IDENTIFIER.match(name):
        raise DataOpError(
            f"{what} '{name}' is not valid. Use letters, digits and underscores, "
            f"starting with a letter."
        )
    return name


def _reserved_tables() -> set[str]:
    """Tables the portal owns. They are never created or overwritten here."""
    return {t.lower() for t in schema.TABLES}


def table_exists(name: str) -> bool:
    found = db.session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table"
        ),
        {"schema": get_config()["database"], "table": name},
    ).scalar()
    return bool(found)


def viewed_tables() -> set[str]:
    """Tables that already have a page of their own, lowercased.

    Derived from the page registry rather than listed by hand, so a new tab
    takes its table out of the 'additional' list automatically. `login` is
    added because its page is MSUsers, which is not in that registry.
    """
    from core.tables import TABLE_PAGES

    return {page["table"].lower() for page in TABLE_PAGES.values()} | {"login"}


def extra_tables() -> list[dict[str, Any]]:
    """Tables in the database that no page in the portal displays."""
    rows = db.session.execute(
        text(
            "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = :schema AND TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_NAME"
        ),
        {"schema": get_config()["database"]},
    ).all()

    skip = viewed_tables()
    return [
        # TABLE_ROWS is InnoDB's estimate; the exact count comes from
        # describe() when a table is opened.
        {"name": name, "rows": int(estimate or 0)}
        for name, estimate in rows
        if name.lower() not in skip
    ]


def describe(table: str) -> dict[str, Any]:
    """Structure and row count of a table with no page of its own.

    Raises:
        DataOpError: unknown table, or one that already has a page.
    """
    table = check_identifier(table, "Table name")
    known = {t["name"] for t in extra_tables()}
    if table not in known:
        raise DataOpError(
            f"'{table}' is not one of the additional tables. Tables with a tab "
            f"of their own are managed there."
        )

    columns = db.session.execute(
        text(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
            "ORDER BY ORDINAL_POSITION"
        ),
        {"schema": get_config()["database"], "table": table},
    ).all()

    # Validated against the catalogue above, so it cannot be anything but a
    # real table name by this point.
    count = db.session.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar()

    return {
        "table": table,
        "rows": int(count or 0),
        "columns": [
            {
                "name": name,
                "type": column_type,
                "nullable": nullable == "YES",
                "key": key == "PRI",
            }
            for name, column_type, nullable, key in columns
        ],
    }


MAX_PAGE = 500


def rows(table: str, limit: int = MAX_PAGE, offset: int = 0) -> dict[str, Any]:
    """A page of data from a table with no page of its own.

    Raises:
        DataOpError: unknown table, or one that already has a page.
    """
    detail = describe(table)               # validates the name against the catalogue
    limit = max(1, min(int(limit or MAX_PAGE), MAX_PAGE))
    offset = max(0, int(offset or 0))

    names = [c["name"] for c in detail["columns"]]
    selected = ", ".join(f"`{n}`" for n in names)
    found = db.session.execute(
        text(f"SELECT {selected} FROM `{table}` LIMIT :limit OFFSET :offset"),
        {"limit": limit, "offset": offset},
    ).mappings().all()

    return {
        "table": table,
        "columns": names,
        "total": detail["rows"],
        "limit": limit,
        "offset": offset,
        # Everything is stringified: this grid only displays, and dates and
        # Decimals are not JSON on their own.
        "rows": [
            ["" if r[n] is None else str(r[n]) for n in names] for r in found
        ],
    }


def _clean_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate the column list coming from the browser."""
    if not columns:
        raise DataOpError("Add at least one column.")
    if len(columns) > MAX_COLUMNS:
        raise DataOpError(f"A table cannot have more than {MAX_COLUMNS} columns.")

    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in columns:
        name = check_identifier(entry.get("name", ""), "Column name")
        if name.lower() in seen:
            raise DataOpError(f"Column '{name}' appears more than once.")
        seen.add(name.lower())

        kind = str(entry.get("type") or "text").strip().lower()
        if kind not in TYPES:
            raise DataOpError(f"Column '{name}' has an unknown type '{kind}'.")

        is_key = bool(entry.get("key"))
        if is_key:
            kind = KEY_SAFE_TYPE.get(kind, kind)

        # Position in the source sheet. Absent for a hand-built table, where
        # the order given is the order used.
        source = entry.get("index")
        try:
            source = int(source) if source is not None else None
        except (TypeError, ValueError):
            raise DataOpError(f"Column '{name}' has an unusable source index.") from None

        cleaned.append(
            {"name": name, "type": kind, "key": is_key, "index": source}
        )

    return cleaned


# ---------------------------------------------------------------------------
# Reading an uploaded sheet
# ---------------------------------------------------------------------------

def sheet_names(stream: BinaryIO, filename: str) -> list[str]:
    """Worksheet names, or [] for a CSV."""
    if not filename.lower().endswith((".xlsx", ".xlsm", ".xltx")):
        return []

    import openpyxl                      # only Excel uploads need it

    book = openpyxl.load_workbook(io.BytesIO(stream.read()), read_only=True)
    try:
        return list(book.sheetnames)
    finally:
        book.close()


def read_sheet(
    stream: BinaryIO, filename: str, sheet: str | None = None
) -> tuple[list[str], list[list[Any]]]:
    """Headers from row 1 and the rows beneath them."""
    if filename.lower().endswith((".xlsx", ".xlsm", ".xltx")):
        names = sheet_names(stream, filename)
        stream.seek(0)
        chosen = sheet or (names[0] if names else "")
        if chosen not in names:
            raise DataOpError(
                f"Sheet '{chosen}' is not in the file. Available: {', '.join(names)}"
            )
        return importer._read_xlsx(stream, chosen, header_row=1)

    return importer._read_csv(stream, header_row=1)


# ---------------------------------------------------------------------------
# Type guessing
# ---------------------------------------------------------------------------

_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}


def _looks_like(values: list[Any]) -> str:
    """The narrowest type every non-blank sample fits."""
    samples = [v for v in values if not importer._is_null(v)]
    if not samples:
        return "text"

    def all_are(test) -> bool:
        return all(test(v) for v in samples)

    if all_are(lambda v: isinstance(v, bool)
               or str(v).strip().lower() in _TRUE | _FALSE):
        return "bool"

    # A whole number only. `_to_int` truncates, so testing with it alone would
    # type a column of 1.5 as BIGINT and silently round the data away.
    def whole(value: Any) -> bool:
        number = importer._to_decimal(value)
        return number is not None and number == number.to_integral_value()

    if all_are(whole):
        return "int"

    if all_are(lambda v: importer._to_decimal(v) is not None):
        return "decimal"

    if all_are(lambda v: isinstance(v, dt.datetime)
               or importer._to_datetime(v) is not None):
        # A date with no time component is a DATE.
        if all_are(lambda v: isinstance(v, dt.date) and not isinstance(v, dt.datetime)
                   or (importer._to_datetime(v) or dt.datetime.now()).time() ==
                   dt.time(0, 0)):
            return "date"
        return "datetime"

    if all_are(lambda v: len(str(v)) <= 255):
        return "text"
    return "longtext"


def _column_name(header: Any, index: int, taken: set[str]) -> str:
    """A usable column name from a sheet header, always unique."""
    raw = "" if header is None else str(header).strip()
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"col_{cleaned}" if cleaned else f"column_{index + 1}"

    candidate = cleaned[:MAX_NAME]
    suffix = 2
    while candidate.lower() in taken:
        candidate = f"{cleaned[:MAX_NAME - 3]}_{suffix}"
        suffix += 1
    taken.add(candidate.lower())
    return candidate


def inspect(
    stream: BinaryIO, filename: str, sheet: str | None = None
) -> dict[str, Any]:
    """What the confirm screen shows: suggested names, types and samples."""
    headers, rows = read_sheet(stream, filename, sheet)
    if not headers:
        raise DataOpError("The first row is empty, so there are no column names.")

    width = max(len(headers), max((len(r) for r in rows[:SAMPLE_ROWS]), default=0))
    taken: set[str] = set()
    columns = []

    for index in range(width):
        header = headers[index] if index < len(headers) else ""
        values = [r[index] if index < len(r) else None for r in rows[:SAMPLE_ROWS]]
        columns.append({
            # Which column of the sheet this came from. Sent back on create so
            # that leaving a column out cannot shift the ones after it.
            "index": index,
            "name": _column_name(header, index, taken),
            "source": "" if header is None else str(header).strip(),
            "type": _looks_like(values),
            "samples": [
                str(v) for v in values[:3] if not importer._is_null(v)
            ],
        })

    return {"columns": columns, "rows": len(rows), "sheet": sheet or ""}


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------

def _ddl(table: str, columns: list[dict[str, Any]]) -> str:
    parts = []
    for column in columns:
        sql_type = TYPES[column["type"]]["sql"]
        null = "NOT NULL" if column["key"] else "NULL"
        parts.append(f"  `{column['name']}` {sql_type} {null}")

    keys = [c["name"] for c in columns if c["key"]]
    if keys:
        parts.append("  PRIMARY KEY (" + ", ".join(f"`{k}`" for k in keys) + ")")

    body = ",\n".join(parts)
    return f"CREATE TABLE `{table}` (\n{body}\n) {TABLE_OPTIONS}".rstrip()


def _value(raw: Any, kind: str) -> Any:
    """One cell, converted for its column. Anything unusable becomes NULL."""
    if importer._is_null(raw):
        return None
    if kind == "int":
        return importer._to_int(raw)
    if kind == "decimal":
        return importer._to_decimal(raw)
    if kind == "bool":
        return importer._to_bool(raw)
    if kind == "date":
        return importer._to_date(raw)
    if kind == "datetime":
        return importer._to_datetime(raw)
    if kind == "text":
        return importer._to_text(raw, 255)
    return str(raw)


def create_table(
    table: str,
    columns: list[dict[str, Any]],
    stream: BinaryIO | None = None,
    filename: str = "",
    sheet: str | None = None,
) -> dict[str, Any]:
    """Create `table`, and load the sheet's rows when one is given.

    Raises:
        DataOpError: a name, type or key the user needs to correct.
    """
    table = check_identifier(table, "Table name")
    if table.lower() in _reserved_tables():
        raise DataOpError(
            f"'{table}' is one of the portal's own tables and cannot be created here."
        )
    if table_exists(table):
        raise DataOpError(f"A table called '{table}' already exists.")

    cleaned = _clean_columns(columns)
    statement = _ddl(table, cleaned)

    rows: list[list[Any]] = []
    if stream is not None:
        _, rows = read_sheet(stream, filename, sheet)

    try:
        db.session.execute(text(statement))
        loaded, skipped = (
            _load(table, cleaned, rows) if rows else (0, 0)
        )
        db.session.commit()
    except DataOpError:
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        logger.exception("Creating table '%s' failed", table)
        raise DataOpError(f"Could not create '{table}': {exc}") from exc

    keys = [c["name"] for c in cleaned if c["key"]]
    logger.info(
        "Created table '%s' with %s column(s), key (%s); loaded %s row(s), skipped %s",
        table, len(cleaned), ", ".join(keys) or "none", loaded, skipped,
    )
    return {
        "table": table,
        "columns": len(cleaned),
        "keys": keys,
        "loaded": loaded,
        "skipped": skipped,
        "ddl": statement,
    }


def _load(
    table: str, columns: list[dict[str, Any]], rows: list[list[Any]]
) -> tuple[int, int]:
    """Insert the sheet's rows. Returns (loaded, skipped)."""
    names = [c["name"] for c in columns]
    params = {c["name"]: f"c{i}" for i, c in enumerate(columns)}
    insert = text(
        f"INSERT INTO `{table}` (" + ", ".join(f"`{n}`" for n in names) + ") "
        "VALUES (" + ", ".join(f":{params[n]}" for n in names) + ")"
    )

    keys = [c for c in columns if c["key"]]
    records: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    skipped = 0

    for raw_row in rows:
        if all(importer._is_null(v) for v in raw_row):
            continue

        record = {}
        for position, column in enumerate(columns):
            # Read from the column's own position in the sheet, not from its
            # position in the chosen subset - otherwise leaving out column 2
            # would pull every later value one field to the left.
            source = column["index"] if column.get("index") is not None else position
            raw = raw_row[source] if source < len(raw_row) else None
            record[params[column["name"]]] = _value(raw, column["type"])

        # A key column cannot be NULL, and the key must be unique - otherwise
        # the whole insert fails and takes the table with it.
        if keys:
            identity = tuple(record[params[c["name"]]] for c in keys)
            if any(v is None for v in identity) or identity in seen:
                skipped += 1
                continue
            seen.add(identity)

        records.append(record)

    for start in range(0, len(records), CHUNK):
        db.session.execute(insert, records[start:start + CHUNK])

    return len(records), skipped
