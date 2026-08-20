"""Generic read layer for the data-table pages.

Columns are read from information_schema rather than hard-coded, so a change in
database/schema.py shows up in the UI without touching this module.

Only tables listed in TABLE_PAGES can be reached. Page keys arrive from the URL
and are never interpolated into SQL - they select a fixed entry from this dict.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from database.db import db, get_config

logger = logging.getLogger(__name__)

# Hard cap so a bad filter can never pull an unbounded result set into memory.
MAX_ROWS: int = 5000

TABLE_PAGES: dict[str, dict[str, Any]] = {
    "all-users": {
        "table": "all_users",
        "title": "All Users",
        "hidden": ("created_at", "updated_at"),
        "order_by": "`userId`",
        "where": None,
        # Rows link to the edit form, keyed on this column.
        "edit_endpoint": "admin.edit_user",
        "edit_key": "userId",
        # Refresh re-applies core.rules across the table before refetching.
        "reconcile_endpoint": "admin.reconcile_all_users",
    },
    "jainam": {
        "table": "jainam",
        "title": "Jainam",
        "hidden": ("created_at",),
        "order_by": "`UserID`",
        # Today's rows, or failing that the most recent earlier date.
        # Future-dated rows are never shown.
        "where": (
            "`Date` = (SELECT MAX(`Date`) FROM `jainam` WHERE `Date` <= CURDATE())"
        ),
        # Shown under the title so it is clear which date is on screen.
        "as_of_sql": "SELECT MAX(`Date`) FROM `jainam` WHERE `Date` <= CURDATE()",
    },
    "running": {
        "table": "running_users",
        "title": "Running",
        "hidden": ("id", "imported_at"),
        # Only these columns are shown, in this order. The rest stay in the
        # table and are still imported - they are just not displayed.
        "visible": (
            "userId", "alias", "max_loss", "allocation",
            "capital", "category", "operator_name", "broker",
        ),
        "order_by": "`userId`",
        # running_users is append-only history; show only the newest import.
        "where": "`imported_at` = (SELECT MAX(`imported_at`) FROM `running_users`)",
    },
    "usersetting": {
        "table": "usersetting",
        "title": "Usersetting",
        "hidden": ("created_at", "updated_at"),
        "order_by": "`User ID`",
        "where": None,
    },
    "server-config": {
        "table": "server_config",
        "title": "Server Config",
        "hidden": ("created_at", "updated_at"),
        # `Dte` is not stored - it counts down daily, so it is derived here.
        # DATEDIFF returns whole days: positive = days left, negative = expired.
        "computed": {"Dte": "DATEDIFF(`Expiry`, CURDATE())"},
        # Rendered as 02-FEB-2026. The raw ISO value is still what gets sorted,
        # so ordering stays chronological rather than alphabetical.
        "formats": {"Expiry": "date"},
        # Sheet order, with the derived Dte back in its original position.
        "visible": (
            "Server", "Username", "IP", "Password", "Stoxxo Id", "Stoxxo Password",
            "Expiry", "Subscriptions", "Logins", "Active", "Avlbl", "Dte",
            "Aum", "Remarks", "Operator", "Stoxxo URL",
        ),
        "order_by": "`Server`",
        "where": None,
    },
}

_NUMERIC_TYPES = {"decimal", "int", "bigint", "smallint", "mediumint", "tinyint", "float", "double"}


def get_page(page_key: str) -> dict[str, Any]:
    """Look up a page config, raising KeyError for anything not whitelisted."""
    return TABLE_PAGES[page_key]


def get_columns(page_key: str) -> list[dict[str, str]]:
    """Visible columns for a page, in table definition order."""
    page = get_page(page_key)
    rows = db.session.execute(
        text(
            "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
            "ORDER BY ORDINAL_POSITION"
        ),
        {"schema": get_config()["database"], "table": page["table"]},
    ).all()

    if not rows:
        logger.error("Table '%s' has no columns - is it provisioned?", page["table"])

    available = {
        name: {"name": name, "type": "number" if data_type in _NUMERIC_TYPES else "text"}
        for name, data_type in rows
        if name not in page["hidden"]
    }

    # Derived columns are not in information_schema; they come from SQL
    # expressions evaluated at read time.
    for name in page.get("computed", {}):
        available[name] = {"name": name, "type": "number", "computed": True}

    # Display formatting hints, applied in the browser at render time.
    for name, fmt in page.get("formats", {}).items():
        if name in available:
            available[name]["format"] = fmt

    whitelist = page.get("visible")
    if not whitelist:
        return list(available.values())

    missing = [name for name in whitelist if name not in available]
    if missing:
        logger.error("Page '%s' lists columns absent from '%s': %s",
                     page_key, page["table"], ", ".join(missing))

    return [available[name] for name in whitelist if name in available]


def as_of(page_key: str) -> str | None:
    """Label describing which slice of the table is on screen, if any."""
    page = get_page(page_key)
    if not page.get("as_of_sql"):
        return None

    value = db.session.execute(text(page["as_of_sql"])).scalar()
    if value is None:
        return "No data uploaded yet"

    today = dt.date.today()
    if isinstance(value, dt.datetime):
        value = value.date()
    if value == today:
        return f"Showing today, {value.isoformat()}"
    return f"No data for today - showing the most recent date, {value.isoformat()}"


def _to_jsonable(value: Any) -> Any:
    """Convert MySQL types the JSON encoder cannot handle."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat(sep=" ") if isinstance(value, dt.datetime) else value.isoformat()
    if isinstance(value, dt.timedelta):  # MySQL TIME comes back as timedelta
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def fetch_rows(page_key: str) -> list[dict[str, Any]]:
    """All visible rows for a page, JSON-safe.

    Filtering and sorting happen in the browser - these tables are in the
    hundreds-to-low-thousands of rows, so shipping them once beats a round trip
    per keystroke.
    """
    page = get_page(page_key)
    columns = get_columns(page_key)
    if not columns:
        return []

    computed = page.get("computed", {})
    select_list = ", ".join(
        f"{computed[c['name']]} AS `{c['name']}`" if c["name"] in computed
        else f"`{c['name']}`"
        for c in columns
    )
    where = f"WHERE {page['where']} " if page.get("where") else ""

    sql = (
        f"SELECT {select_list} FROM `{page['table']}` {where}"
        f"ORDER BY {page['order_by']} LIMIT {MAX_ROWS}"
    )

    rows = db.session.execute(text(sql)).mappings().all()
    logger.info("Loaded %s rows for page '%s'", len(rows), page_key)

    return [{k: _to_jsonable(v) for k, v in row.items()} for row in rows]
