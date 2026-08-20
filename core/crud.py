"""Generic create / single-field update for the editable table pages.

Both All Users and Server Config need "add a row" and "edit one cell", and the
only differences between them are the table, the key, and whether business
rules apply. That variation lives in EDITABLE below rather than in two
near-identical modules.

Writes go through `core.rules.apply` when the page declares it, so an inline
cell edit is subject to exactly the same rules as the full edit form.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from sqlalchemy import text

from core import derive, rules
from core.importer import _coerce, _target_columns
from core.tables import _to_jsonable
from database.db import db

logger = logging.getLogger(__name__)

# Maintained by MySQL; never written from a form.
AUTO_COLUMNS = {"id", "created_at", "updated_at", "imported_at"}


class EditableSpec:
    def __init__(
        self,
        table: str,
        pk: str,
        derived: tuple[str, ...] = (),
        post_process: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        options: dict[str, tuple[str, ...]] | None = None,
        dynamic_options: dict[str, str] | None = None,
        scope: str | None = None,
        row_key: str | None = None,
    ):
        self.table = table
        self.pk = pk
        # Computed by the rules - shown but never accepted from the client.
        self.derived = derived
        self.post_process = post_process
        self.options = options or {}
        # column -> SQL returning the distinct values to offer.
        self.dynamic_options = dynamic_options or {}
        # Extra WHERE fragment limiting which rows a *new* row may collide
        # with. Not applied to updates, which are keyed on `row_key`.
        self.scope = scope
        # Column that identifies one row for editing. Defaults to the business
        # key; all_users needs `id` because userId repeats across dates.
        self.row_key = row_key or pk

    @property
    def key_column(self) -> str:
        return self.row_key


EDITABLE: dict[str, EditableSpec] = {
    "all-users": EditableSpec(
        table="all_users",
        pk="userId",
        derived=("ml_pct", "Operator Name", "Date"),
        # userId repeats across dates, so a row is identified by its surrogate
        # id. Without this an edit would be ambiguous once a second date is
        # loaded, and impossible on any date but the newest.
        row_key="id",
        # A new row must not collide with one already held for that date.
        scope="`Date` = (SELECT MAX(`Date`) FROM `all_users` WHERE `Date` <= CURDATE())",
        post_process=derive.apply_all_users,
        options={
            "Running Type": rules.RUNNING_TYPE_OPTIONS,
            "Running Days": rules.RUNNING_DAYS_OPTIONS,
        },
        dynamic_options={
            "server": "SELECT DISTINCT `server` FROM `all_users` WHERE `server` IS NOT NULL"
        },
    ),
    "server-config": EditableSpec(
        table="server_config",
        pk="Server",
    ),
}


def get_spec(page_key: str) -> EditableSpec:
    return EDITABLE[page_key]


def _param(name: str) -> str:
    return "v_" + re.sub(r"\W", "_", name)


def _scope(spec: EditableSpec) -> str:
    """WHERE suffix limiting a page to its editable rows."""
    return f" AND {spec.scope}" if spec.scope else ""


def form_columns(page_key: str) -> list[dict[str, Any]]:
    """Columns a form may write, with the metadata needed to render them."""
    spec = get_spec(page_key)
    columns = []

    for column in _target_columns(spec.table):
        if column["name"] in spec.derived:
            continue
        columns.append(
            {
                **column,
                "is_pk": column["name"] == spec.pk,
                "options": list(spec.options.get(column["name"], ())),
                "input_type": _input_type(column),
            }
        )

    for name, sql in spec.dynamic_options.items():
        target = next((c for c in columns if c["name"] == name), None)
        if target is None:
            continue
        values = db.session.execute(text(sql)).scalars().all()
        known = {str(v).strip() for v in values if str(v or "").strip()}
        known.update(rules.INACTIVE_STATES)
        target["options"] = [
            *rules.INACTIVE_STATES,
            *sorted(v for v in known if v not in rules.INACTIVE_STATES),
        ]

    return columns


def _input_type(column: dict[str, Any]) -> str:
    if column["is_bool"]:
        return "checkbox"
    if column["data_type"] == "date":
        return "date"
    if column["data_type"] in ("decimal", "int", "bigint", "float", "double"):
        return "number"
    return "text"


def editable_meta(page_key: str) -> dict[str, Any]:
    """What the browser needs to render inline editors: which columns are
    editable, and the option list for any that are dropdowns."""
    spec = get_spec(page_key)
    columns = form_columns(page_key)

    return {
        # The browser sends this column's value to identify the row.
        "pk": spec.key_column,
        "readonly": ["id", spec.pk, *spec.derived],
        "options": {c["name"]: c["options"] for c in columns if c["options"]},
        "types": {c["name"]: c["input_type"] for c in columns},
    }


def get_row(page_key: str, key: str, column: str | None = None) -> dict[str, Any] | None:
    """One row, looked up by `column` (default: the row key).

    The page scope is applied only when looking up by the business key, which
    is the duplicate check for a new row. Editing is keyed on `row_key`, which
    is unique on its own.
    """
    spec = get_spec(page_key)
    column = column or spec.key_column
    where = _scope(spec) if column == spec.pk else ""

    row = db.session.execute(
        text(f"SELECT * FROM `{spec.table}` WHERE `{column}` = :pk{where}"),
        {"pk": key},
    ).mappings().first()
    return {k: _to_jsonable(v) for k, v in row.items()} if row else None


def create_row(page_key: str, form: dict[str, Any]) -> str:
    """Insert one row from form input.

    Returns:
        The new row's primary key.

    Raises:
        ValueError: missing key, or that key already exists.
    """
    spec = get_spec(page_key)
    columns = form_columns(page_key)

    key = (form.get(spec.pk) or "").strip()
    if not key:
        raise ValueError(f"{spec.pk} is required.")
    if get_row(page_key, key, column=spec.pk) is not None:
        raise ValueError(f"{spec.pk} '{key}' already exists.")

    values = {c["name"]: _coerce(form.get(c["name"]), c) for c in columns}
    values[spec.pk] = key

    if spec.post_process:
        # Derived columns are produced here, not taken from the client.
        values.update({name: None for name in spec.derived})
        spec.post_process(values)

    names = list(values)
    statement = (
        f"INSERT INTO `{spec.table}` ({', '.join(f'`{n}`' for n in names)}) "
        f"VALUES ({', '.join(f':{_param(n)}' for n in names)})"
    )

    try:
        db.session.execute(text(statement), {_param(n): values[n] for n in names})
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Insert into '%s' failed for key '%s'", spec.table, key)
        raise

    logger.info("Created %s row '%s'", spec.table, key)
    return key


def update_field(page_key: str, key: str, column_name: str, raw_value: Any) -> dict[str, Any]:
    """Update one column of one row, then re-apply the page's rules.

    Returns:
        The full row as stored, so the caller can refresh derived and linked
        columns that the edit may have changed.

    Raises:
        LookupError: no such row.
        ValueError: the column is unknown, derived, or the primary key.
    """
    spec = get_spec(page_key)
    columns = {c["name"]: c for c in form_columns(page_key)}

    if column_name in (spec.pk, spec.key_column):
        raise ValueError(f"{column_name} cannot be changed.")
    if column_name in spec.derived:
        raise ValueError(f"{column_name} is calculated and cannot be edited.")
    if column_name not in columns:
        raise ValueError(f"Unknown column '{column_name}'.")

    existing = get_row(page_key, key)
    if existing is None:
        raise LookupError(f"No row with {spec.key_column} '{key}'")

    values = {column_name: _coerce(raw_value, columns[column_name])}

    if spec.post_process:
        # The rules need the whole row: changing one linked field rewrites
        # its siblings, and ml_pct depends on max_loss and allocation.
        merged = {**existing, **values}
        spec.post_process(merged)
        managed = set(spec.derived) | set(rules.LINKED_FIELDS) | {"algo", "Operator Name"}
        values = {
            name: merged[name]
            for name in managed | {column_name}
            if name in merged and name not in (spec.pk, spec.key_column)
        }

    assignments = ", ".join(f"`{n}` = :{_param(n)}" for n in values)
    params = {_param(n): v for n, v in values.items()}
    params["pk"] = key

    try:
        db.session.execute(
            text(f"UPDATE `{spec.table}` SET {assignments} "
                 f"WHERE `{spec.key_column}` = :pk"),
            params,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Inline update failed: %s.%s for '%s'", spec.table, column_name, key)
        raise

    logger.info(
        "Inline edit %s[%s=%s].%s -> %r (wrote %s column(s))",
        spec.table, spec.key_column, key, column_name,
        values.get(column_name), len(values),
    )
    return get_row(page_key, key)
