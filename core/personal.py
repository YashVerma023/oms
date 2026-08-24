"""The Personal view, rebuilt from All Users.

Nothing is uploaded here. One row is written per all_users account whose
SubCategory is one of `ACCOUNT_TYPES`, for a chosen date.

Columns are matched by name at run time rather than being listed in code:
every column of `personal` is filled from the all_users column of the same
name, ignoring case, spaces and underscores. Two are special:

    Account Type  <- SubCategory   (the value that selected the row)
    Date          <- the date being rebuilt

A column with no match in all_users is left NULL and reported, so adding a
column to `personal` never silently produces blanks nobody notices.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import bindparam, text

from core.tables import column_names
from database.db import db, get_config

logger = logging.getLogger(__name__)

TABLE = "personal"
SOURCE = "all_users"

# The SubCategory values that make an account personal.
ACCOUNT_TYPES = ("PGB", "PVT", "PPS", "PRD")

# Compared with `_key`, so `Account Type`, `Account_Type` and `accounttype`
# are all the same column.
ACCOUNT_TYPE_COLUMN = "accounttype"
DATE_COLUMN = "date"

# Filled by the database itself.
AUTO_COLUMNS = {"id", "created_at", "updated_at"}

# Personal column -> all_users column, for pairs whose names genuinely differ.
# Both sides are folded with `_key`, so only real differences belong here -
# spacing, case and underscores are handled already. `ml_pct` needs no entry:
# the two names match once folded.
SYNONYMS: dict[str, str] = {
    "runningday": "runningdays",     # Running_Day  <- Running Days
    "operator": "operatorname",      # Operator     <- Operator Name
}


class PersonalError(ValueError):
    """Something the user needs to fix, safe to show them."""


def _key(name: str) -> str:
    """Match names across spacing and case: 'Max Loss' == 'max_loss'."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _columns(table: str) -> list[str]:
    """Column names of `table`, in table order."""
    return db.session.execute(
        text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
            "ORDER BY ORDINAL_POSITION"
        ),
        {"schema": get_config()["database"], "table": table},
    ).scalars().all()


def plan(on_date: dt.date | None = None) -> dict[str, Any]:
    """How each Personal column would be filled. Reads only.

    Returns:
        {"mapped": {personal column: all_users column}, "unmapped": [...],
         "date_column": name or None}
    """
    target = [c for c in _columns(TABLE) if c.lower() not in AUTO_COLUMNS]
    if not target:
        raise PersonalError(
            f"Table '{TABLE}' does not exist, or has no columns. Create it "
            f"under Data Operation first."
        )

    available = {_key(c): c for c in _columns(SOURCE)}
    mapped: dict[str, str] = {}
    unmapped: list[str] = []
    dated: str | None = None

    for column in target:
        folded = _key(column)
        if folded == DATE_COLUMN:
            dated = column
            continue
        if folded == ACCOUNT_TYPE_COLUMN:
            source = available.get(_key("SubCategory"))
            if source:
                mapped[column] = source
            else:
                unmapped.append(column)
            continue

        source = available.get(folded) or available.get(SYNONYMS.get(folded, ""))
        if source:
            mapped[column] = source
        else:
            unmapped.append(column)

    return {"mapped": mapped, "unmapped": unmapped, "date_column": dated}


def rebuild(on_date: dt.date | None = None) -> dict[str, Any]:
    """Replace `on_date`'s Personal rows with what All Users says today.

    Raises:
        PersonalError: the table is missing, has no Date column, or nothing
            in it can be filled.
    """
    on_date = on_date or dt.date.today()
    shape = plan(on_date)

    if not shape["date_column"]:
        raise PersonalError(
            f"Table '{TABLE}' has no Date column yet. Add one "
            f"(ALTER TABLE `{TABLE}` ADD COLUMN `Date` DATE NOT NULL) and "
            f"refresh again."
        )
    if not shape["mapped"]:
        raise PersonalError(
            f"None of the columns in '{TABLE}' match a column in "
            f"'{SOURCE}', so there is nothing to copy."
        )

    targets = list(shape["mapped"]) + [shape["date_column"]]
    sources = [f"a.`{shape['mapped'][c]}`" for c in shape["mapped"]] + [":d"]

    columns = ", ".join(f"`{c}`" for c in targets)
    selected = ", ".join(sources)

    try:
        removed = db.session.execute(
            text(f"DELETE FROM `{TABLE}` WHERE `{shape['date_column']}` = :d"),
            {"d": on_date},
        ).rowcount

        # INSERT ... SELECT: the rows never travel through Python.
        statement = text(
            f"INSERT INTO `{TABLE}` ({columns}) SELECT {selected} "
            f"FROM `{SOURCE}` AS a "
            f"WHERE a.`Date` = :d AND UPPER(TRIM(a.`SubCategory`)) IN :types"
        ).bindparams(bindparam("types", expanding=True))

        written = db.session.execute(
            statement, {"d": on_date, "types": list(ACCOUNT_TYPES)}
        ).rowcount
        db.session.commit()
    except PersonalError:
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        logger.exception("Rebuilding '%s' for %s failed", TABLE, on_date)
        raise PersonalError(f"Could not rebuild Personal: {exc}") from exc

    logger.info(
        "Personal rebuilt for %s: %s row(s) written, %s replaced; "
        "unmapped column(s): %s",
        on_date, written, removed, ", ".join(shape["unmapped"]) or "none",
    )
    return {
        "date": on_date.isoformat(),
        "written": written,
        "replaced": removed,
        "unmapped": shape["unmapped"],
        "account_types": list(ACCOUNT_TYPES),
    }
