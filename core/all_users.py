"""Read/update helpers for the all_users record.

Every write goes through `update_user`, so the rules in core.rules are applied
in one place rather than being re-implemented per caller.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text

from core import rules
from core.tables import _to_jsonable
from database.db import db, get_config

logger = logging.getLogger(__name__)

TABLE = "all_users"
PK = "userId"

# Never writable from the form.
READONLY_COLUMNS = {PK, "ml_pct", "created_at", "updated_at"}


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


def get_user(user_id: str) -> dict[str, Any] | None:
    row = db.session.execute(
        text(f"SELECT * FROM `{TABLE}` WHERE `{PK}` = :pk"),
        {"pk": user_id},
    ).mappings().first()
    return {k: _to_jsonable(v) for k, v in row.items()} if row else None


def server_options() -> list[str]:
    """Servers already in use, plus the two inactive markers."""
    rows = db.session.execute(
        text(f"SELECT DISTINCT `server` FROM `{TABLE}` WHERE `server` IS NOT NULL")
    ).scalars().all()

    known = {str(value).strip() for value in rows if str(value).strip()}
    known.update(rules.INACTIVE_STATES)
    # Inactive markers first, then the rest alphabetically.
    rest = sorted(v for v in known if v not in rules.INACTIVE_STATES)
    return [*rules.INACTIVE_STATES, *rest]


def update_user(user_id: str, form: dict[str, Any]) -> dict[str, Any]:
    """Apply the business rules and write one row.

    Returns:
        The values as stored, so the caller can report what the rules changed.

    Raises:
        LookupError: no such user.
    """
    existing = get_user(user_id)
    if existing is None:
        raise LookupError(f"No user '{user_id}'")

    columns = editable_columns()
    values: dict[str, Any] = {}

    for column in columns:
        raw = form.get(column["name"])
        if raw is None:
            values[column["name"]] = existing.get(column["name"])
            continue
        raw = raw.strip() if isinstance(raw, str) else raw
        values[column["name"]] = _parse(raw, column)

    # ml_pct is derived, so it is computed here rather than read from the form.
    values["ml_pct"] = existing.get("ml_pct")
    rules.apply(values)

    assignments = ", ".join(f"`{name}` = :{_param(name)}" for name in values)
    params = {_param(name): value for name, value in values.items()}
    params["pk"] = user_id

    try:
        db.session.execute(
            text(f"UPDATE `{TABLE}` SET {assignments} WHERE `{PK}` = :pk"), params
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Update failed for user '%s'", user_id)
        raise

    logger.info(
        "Updated user '%s' (server=%s, running_type=%s, algo=%s, ml_pct=%s)",
        user_id, values.get("server"), values.get("Running Type"),
        values.get("algo"), values.get("ml_pct"),
    )
    return values


# Fields the rules own. Only these are rewritten by a reconcile.
RULE_FIELDS = ("server", "Running Type", "Running Days", "algo", "ml_pct")

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
    rows = db.session.execute(
        text(
            "SELECT `userId`, `server`, `Running Type`, `Running Days`, "
            "`algo`, `max_loss`, `allocation`, `ml_pct` FROM `all_users`"
        )
    ).mappings().all()

    updates: list[dict[str, Any]] = []
    inactive = 0

    for row in rows:
        before = dict(row)
        after = rules.apply(dict(row))

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
                text(f"UPDATE `{TABLE}` SET {assignments} WHERE `{PK}` = :pk"), updates
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
