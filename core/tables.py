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

from sqlalchemy import bindparam, text

from database.db import db, get_config

logger = logging.getLogger(__name__)

# Hard cap so a bad filter can never pull an unbounded result set into memory.
MAX_ROWS: int = 5000

TABLE_PAGES: dict[str, dict[str, Any]] = {
    "all-users": {
        "table": "all_users",
        # Rows are identified by the surrogate id: userId alone is no longer
        # unique now that snapshots share it.
        "delete_key": ("id",),
        "title": "All Users",
        "hidden": ("id", "created_at", "updated_at"),
        "order_by": "`userId`",
        # One day at a time. `date_column` puts a calendar in the toolbar;
        # without a chosen date the newest date not in the future is shown.
        "date_column": "Date",
        "where": None,
        # Rows link to the edit form, keyed on this column.
        "edit_endpoint": "admin.edit_user",
        # Keyed on the surrogate id so the form opens the exact row shown,
        # on any date.
        "edit_key": "id",
        # Refresh re-applies core.rules across the table before refetching.
        "reconcile_endpoint": "admin.reconcile_all_users",
        # Filtered by ticking values rather than typing. Options are the
        # distinct values present in the loaded rows.
        "choice_filters": ("server", "algo"),
    },
    "jainam": {
        "table": "jainam",
        "delete_key": ("Date", "UserID"),
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
        # Jainam carries no server column, so ownership is resolved through
        # all_users: the user's server decides whose row it is.
        "operator_scope": (
            "`UserID` IN (SELECT `userId` FROM `all_users` WHERE `server` IN :servers)"
        ),
    },
    "running": {
        "table": "running_users",
        "delete_key": ("id",),
        "title": "Running",
        "hidden": ("id", "imported_at"),
        # Only these columns are shown, in this order. The rest stay in the
        # table and are still imported - they are just not displayed.
        "visible": (
            "userId", "alias", "server", "algo", "max_loss", "allocation",
            "capital", "category", "operator_name", "broker",
        ),
        "choice_filters": ("server", "algo"),
        "order_by": "`userId`",
        # running_users is append-only history; show only the newest import.
        "where": "`imported_at` = (SELECT MAX(`imported_at`) FROM `running_users`)",
    },
    "usersetting": {
        "table": "usersetting",
        "delete_key": ("User ID",),
        "title": "Usersetting",
        "hidden": ("created_at", "updated_at"),
        "order_by": "`server`, `User ID`",
        "where": None,
        # `server` comes from the uploaded file name; `algo` is copied from
        # all_users, matched on user id.
        "choice_filters": ("server", "algo"),
        # Operators see only their own servers.
        "operator_scope": "`server` IN :servers",
        # These three sit at the end of the table but belong next to Broker on
        # screen, so they are moved without touching the schema.
        "reorder": {"after": "Broker", "columns": ("server", "algo", "Remarks")},
        # Refresh re-derives algo before refetching.
        "reconcile_endpoint": "admin.reconcile_usersetting",
    },
    "server-config": {
        "table": "server_config",
        "delete_key": ("Server",),
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

    # Columns filtered by ticking values instead of typing.
    for name in page.get("choice_filters", ()):
        if name in available:
            available[name]["choice"] = True

    whitelist = page.get("visible")
    if not whitelist:
        return _reordered(list(available.values()), page)

    missing = [name for name in whitelist if name not in available]
    if missing:
        logger.error("Page '%s' lists columns absent from '%s': %s",
                     page_key, page["table"], ", ".join(missing))

    return _reordered([available[name] for name in whitelist if name in available], page)


def _reordered(columns: list[dict], page: dict[str, Any]) -> list[dict]:
    """Move a page's `reorder` columns to just after its anchor column."""
    spec = page.get("reorder")
    if not spec:
        return columns

    moving = [c for c in columns if c["name"] in spec["columns"]]
    if not moving:
        return columns

    rest = [c for c in columns if c["name"] not in spec["columns"]]
    anchor = next((i for i, c in enumerate(rest) if c["name"] == spec["after"]), None)
    if anchor is None:
        logger.error("Reorder anchor '%s' is not a visible column", spec["after"])
        return columns

    # Keep the order given in the config, not the table order.
    order = {name: i for i, name in enumerate(spec["columns"])}
    moving.sort(key=lambda c: order[c["name"]])
    return rest[: anchor + 1] + moving + rest[anchor + 1 :]


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


def delete_rows(page_key: str, keys: list[list[Any]]) -> int:
    """Delete rows identified by their `delete_key` values.

    Args:
        keys: one list of key-column values per row, in `delete_key` order.

    Returns:
        Number of rows deleted.

    Raises:
        ValueError: the page has no delete key, or a key has the wrong arity.
    """
    page = get_page(page_key)
    key_columns = page.get("delete_key")
    if not key_columns:
        raise ValueError(f"Page '{page_key}' does not support deletion.")

    if not keys:
        return 0

    bad = [k for k in keys if not isinstance(k, list) or len(k) != len(key_columns)]
    if bad:
        raise ValueError(
            f"Each key needs {len(key_columns)} value(s) "
            f"({', '.join(key_columns)}); got {bad[0]!r}"
        )

    where = " AND ".join(f"`{c}` = :k{i}" for i, c in enumerate(key_columns))
    statement = text(f"DELETE FROM `{page['table']}` WHERE {where}")
    params = [{f"k{i}": value for i, value in enumerate(key)} for key in keys]

    try:
        deleted = db.session.execute(statement, params).rowcount
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Delete from '%s' failed; no rows removed", page["table"])
        raise

    logger.info(
        "Deleted %s row(s) from '%s' (%s requested)", deleted, page["table"], len(keys)
    )
    return deleted


def available_dates(page_key: str) -> list[str]:
    """Dates this page holds data for, newest first."""
    page = get_page(page_key)
    column = page.get("date_column")
    if not column:
        return []

    rows = db.session.execute(
        text(
            f"SELECT DISTINCT `{column}` FROM `{page['table']}` "
            f"WHERE `{column}` IS NOT NULL ORDER BY `{column}` DESC"
        )
    ).scalars().all()
    return [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in rows]


def default_date(page_key: str) -> str | None:
    """Always today for a dated page.

    Deliberately not "the newest date with data": the tab should open on today
    every time, showing an empty table if nothing has been uploaded yet, rather
    than silently presenting an older day as if it were current.
    """
    if not get_page(page_key).get("date_column"):
        return None
    return dt.date.today().isoformat()


def fetch_rows(
    page_key: str,
    on_date: str | None = None,
    servers: list[str] | None = None,
) -> list[dict[str, Any]]:
    """All visible rows for a page, JSON-safe.

    Filtering and sorting happen in the browser - these tables are in the
    hundreds-to-low-thousands of rows, so shipping them once beats a round trip
    per keystroke.
    """
    page = get_page(page_key)
    columns = get_columns(page_key)
    if not columns:
        return []

    # A dated page shows exactly one day.
    params: dict[str, Any] = {}
    statement_params: list[Any] = []
    date_clause = ""
    if page.get("date_column"):
        chosen = on_date or default_date(page_key)
        if chosen is None:
            return []
        date_clause = f"`{page['date_column']}` = :on_date"
        params["on_date"] = chosen

    # Operator restriction. An operator with no servers sees nothing, rather
    # than falling through to the unrestricted query.
    scope_clause = ""
    if servers is not None and page.get("operator_scope"):
        if not servers:
            logger.info("Page '%s': operator has no servers - returning nothing", page_key)
            return []
        scope_clause = page["operator_scope"]
        params["servers"] = servers
        statement_params.append(bindparam("servers", expanding=True))

    computed = page.get("computed", {})
    parts = [
        f"{computed[c['name']]} AS `{c['name']}`" if c["name"] in computed
        else f"`{c['name']}`"
        for c in columns
    ]

    # The delete key must travel with each row even when it is not displayed
    # (running_users identifies rows by the hidden `id`).
    shown = {c["name"] for c in columns}
    for key_column in page.get("delete_key", ()):
        if key_column not in shown:
            parts.append(f"`{key_column}`")

    select_list = ", ".join(parts)
    conditions = [c for c in (page.get("where"), date_clause, scope_clause) if c]
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""

    sql = (
        f"SELECT {select_list} FROM `{page['table']}` {where}"
        f"ORDER BY {page['order_by']} LIMIT {MAX_ROWS}"
    )

    statement = text(sql)
    if statement_params:
        statement = statement.bindparams(*statement_params)

    rows = db.session.execute(statement, params).mappings().all()
    logger.info(
        "Loaded %s rows for page '%s'%s",
        len(rows), page_key,
        f" (date {params['on_date']})" if "on_date" in params else "",
    )

    return [{k: _to_jsonable(v) for k, v in row.items()} for row in rows]
