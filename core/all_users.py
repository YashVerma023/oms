"""Read/update helpers for the all_users record.

Every write goes through `update_user`, so the rules in core.rules are applied
in one place rather than being re-implemented per caller.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text

from core import derive, rules
from core.tables import _to_jsonable
from database.db import db, get_config

logger = logging.getLogger(__name__)

TABLE = "all_users"
PK = "userId"

# Everything that reads one authoritative row per user - edits, the reconcile,
# the operator sync, the usersetting join - uses the most recent date that is
# not in the future. Older dates are history and are only ever displayed.
CURRENT_DATE_SQL = (
    "(SELECT MAX(`Date`) FROM `all_users` WHERE `Date` <= CURDATE())"
)
WORKING = f"`Date` = {CURRENT_DATE_SQL}"

# Never writable from the form.
READONLY_COLUMNS = {"id", PK, "ml_pct", "Operator Name", "Date", "created_at", "updated_at"}



def editable_columns() -> list[dict[str, Any]]:
    """Columns the edit form may write, in table order."""
    rows = db.session.execute(
        text(
            "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
            "ORDER BY ORDINAL_POSITION"
        ),
        {"schema": get_config()["database"], "table": TABLE},
    ).all()

    return [
        {
            "name": name,
            "numeric": data_type in ("decimal", "int", "bigint", "float", "double"),
            "maxlength": int(length) if length else 0,
        }
        for name, data_type, length in rows
        if name not in READONLY_COLUMNS
    ]


# Rows are identified by the surrogate id: userId repeats across dates, so it
# no longer identifies a single row on its own.
ROW_KEY = "id"


def get_user(row_id: str) -> dict[str, Any] | None:
    """One all_users row by its id, whichever date it belongs to."""
    row = db.session.execute(
        text(f"SELECT * FROM `{TABLE}` WHERE `{ROW_KEY}` = :pk"),
        {"pk": row_id},
    ).mappings().first()
    return {k: _to_jsonable(v) for k, v in row.items()} if row else None


def server_options() -> list[str]:
    """Servers already in use, plus the two inactive markers."""
    rows = db.session.execute(
        text(f"SELECT DISTINCT `server` FROM `{TABLE}` "
             f"WHERE `server` IS NOT NULL AND {WORKING}")
    ).scalars().all()

    known = {str(value).strip() for value in rows if str(value).strip()}
    known.update(rules.INACTIVE_STATES)
    # Inactive markers first, then the rest alphabetically.
    rest = sorted(v for v in known if v not in rules.INACTIVE_STATES)
    return [*rules.INACTIVE_STATES, *rest]


def update_user(row_id: str, form: dict[str, Any]) -> dict[str, Any]:
    """Apply the business rules and write one row.

    Returns:
        The values as stored, so the caller can report what the rules changed.

    Raises:
        LookupError: no such user.
    """
    existing = get_user(row_id)
    if existing is None:
        raise LookupError(f"No row with id '{row_id}'")

    columns = editable_columns()
    values: dict[str, Any] = {}

    for column in columns:
        raw = form.get(column["name"])
        if raw is None:
            values[column["name"]] = existing.get(column["name"])
            continue
        raw = raw.strip() if isinstance(raw, str) else raw
        values[column["name"]] = _parse(raw, column)

    # ml_pct and Operator Name are derived, not read from the form. `Date` is
    # seeded from the row being edited: apply_all_users stamps today when it is
    # missing, which would silently move an older row onto today's date.
    values["ml_pct"] = existing.get("ml_pct")
    values["Date"] = existing.get("Date")
    derive.apply_all_users(values)

    # Belt and braces - the date of an existing row is never rewritten.
    values.pop("Date", None)

    assignments = ", ".join(f"`{name}` = :{_param(name)}" for name in values)
    params = {_param(name): value for name, value in values.items()}
    params["pk"] = row_id

    try:
        db.session.execute(
            text(f"UPDATE `{TABLE}` SET {assignments} WHERE `{ROW_KEY}` = :pk"),
            params
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Update failed for user '%s'", user_id)
        raise

    logger.info(
        "Updated all_users id=%s (server=%s, running_type=%s, algo=%s, ml_pct=%s, operator=%s)",
        row_id, values.get("server"), values.get("Running Type"),
        values.get("algo"), values.get("ml_pct"), values.get("Operator Name"),
    )
    return values


# Fields the rules own. Only these are rewritten by a reconcile.
RULE_FIELDS = ("server", "Running Type", "Running Days", "algo", "ml_pct", "Operator Name")

# ml_pct is DECIMAL(18,4); compare at that scale so a reconcile does not
# report the same rows as changed on every run.
_ML_PCT_SCALE = Decimal(1).scaleb(-4)


def _same(before: Any, after: Any) -> bool:
    if before is None or after is None:
        return before is None and after is None
    if isinstance(before, Decimal) or isinstance(after, Decimal):
        try:
            return (Decimal(str(before)).quantize(_ML_PCT_SCALE)
                    == Decimal(str(after)).quantize(_ML_PCT_SCALE))
        except (InvalidOperation, ValueError):
            return str(before) == str(after)
    return str(before) == str(after)


def reconcile_all() -> dict[str, int]:
    """Re-apply the business rules to every row and persist any corrections.

    Rows edited outside the portal - a direct SQL update, or a sheet loaded
    before a rule existed - can drift out of line with the rules. This brings
    the whole table back into agreement with `core.rules`.

    Only the rule-owned fields are written; everything else is left alone.

    Returns:
        Counts of rows checked, rows updated, and rows currently inactive.
    """
    # server_config may have changed since the cache was filled.
    derive.operator_map.cache_clear()

    rows = db.session.execute(
        text(
            "SELECT `userId`, `server`, `Running Type`, `Running Days`, "
            "`algo`, `max_loss`, `allocation`, `ml_pct`, `Operator Name` "
            f"FROM `all_users` WHERE {WORKING}"
        )
    ).mappings().all()

    updates: list[dict[str, Any]] = []
    inactive = 0

    for row in rows:
        before = dict(row)
        after = derive.apply_all_users(dict(row))

        if rules.inactive_state(after):
            inactive += 1

        if any(not _same(before.get(f), after.get(f)) for f in RULE_FIELDS):
            params = {_param(f): after.get(f) for f in RULE_FIELDS}
            params["pk"] = row["userId"]
            updates.append(params)

    if updates:
        assignments = ", ".join(f"`{f}` = :{_param(f)}" for f in RULE_FIELDS)
        try:
            db.session.execute(
                text(f"UPDATE `{TABLE}` SET {assignments} WHERE `{PK}` = :pk AND {WORKING}"),
                updates
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Reconcile failed; no changes were written")
            raise

    logger.info(
        "Reconciled all_users: %s checked, %s updated, %s inactive",
        len(rows), len(updates), inactive,
    )
    return {"checked": len(rows), "updated": len(updates), "inactive": inactive}


def working_count() -> int:
    """How many rows are in the editable working set."""
    return db.session.execute(
        text(f"SELECT COUNT(*) FROM `{TABLE}` WHERE {WORKING}")
    ).scalar() or 0


def snapshot_dates() -> list[str]:
    """Dates that already hold a saved snapshot, newest first."""
    rows = db.session.execute(
        text(f"SELECT DISTINCT `Date` FROM `{TABLE}` "
             "WHERE `Date` IS NOT NULL ORDER BY `Date` DESC")
    ).scalars().all()
    # Drivers differ on whether DATE arrives as a date object or a string.
    return [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in rows]


def save_snapshot(on_date: dt.date) -> dict[str, Any]:
    """Store the working set as the snapshot for `on_date`.

    Any snapshot already held for that date is replaced. The working set is
    left untouched, so editing continues from where it was.

    Returns:
        Counts of rows saved and rows replaced.

    Raises:
        ValueError: the working set is empty - there is nothing to save.
    """
    columns = [
        name
        for name, in db.session.execute(
            text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
                "ORDER BY ORDINAL_POSITION"
            ),
            {"schema": get_config()["database"], "table": TABLE},
        ).all()
        if name not in ("id", "Date", "created_at", "updated_at")
    ]

    working = db.session.execute(
        text(f"SELECT COUNT(*) FROM `{TABLE}` WHERE {WORKING}")
    ).scalar()
    if not working:
        raise ValueError("There are no working rows to save. Upload All Users first.")

    column_list = ", ".join(f"`{c}`" for c in columns)

    try:
        replaced = db.session.execute(
            text(f"DELETE FROM `{TABLE}` WHERE `Date` = :d"), {"d": on_date}
        ).rowcount

        db.session.execute(
            text(
                f"INSERT INTO `{TABLE}` ({column_list}, `Date`) "
                f"SELECT {column_list}, :d FROM `{TABLE}` WHERE {WORKING}"
            ),
            {"d": on_date},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Saving the all_users snapshot for %s failed", on_date)
        raise

    logger.info(
        "Saved all_users snapshot for %s: %s row(s) written, %s replaced",
        on_date, working, replaced,
    )
    return {"date": on_date.isoformat(), "saved": working, "replaced": replaced}


def _parse(raw: Any, column: dict[str, Any]) -> Any:
    """Empty form fields become NULL; numeric fields are parsed, not stored as text."""
    if raw is None or (isinstance(raw, str) and not raw):
        return None
    if not column["numeric"]:
        return raw
    from core.importer import _to_decimal  # shared coercion, avoids a duplicate parser

    return _to_decimal(raw)


def _param(name: str) -> str:
    return "v_" + re.sub(r"\W", "_", name)
