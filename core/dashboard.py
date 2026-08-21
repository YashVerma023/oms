"""Dashboard pivot: algo -> server -> subcategory -> user.

Built from `all_users` for one date. Algo 0 is excluded - those are dealer and
parked accounts, not live algos.

Counts at every level are **distinct users**, so a level's total always equals
the sum of the level below it.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import bindparam, text

from core import rules_io
from database.db import db

logger = logging.getLogger(__name__)

# Top-level categories that get their own count column.
CATEGORIES = ("CC", "MS")

# Algos that are not real strategies.
EXCLUDED_ALGOS = ("0",)

DTE_COLUMNS = rules_io.DTE_COLUMNS


def _rows(
    on_date: dt.date,
    servers: list[str] | None,
    dte: dict[str, list[str]],
) -> list[dict[str, Any]]:
    sql = (
        "SELECT `userId`, `alias`, `algo`, `server`, `Category`, `SubCategory` "
        "FROM `all_users` WHERE `Date` = :d "
        "AND TRIM(COALESCE(`algo`, '')) NOT IN :excluded "
    )
    params: dict[str, Any] = {"d": on_date, "excluded": [*EXCLUDED_ALGOS, ""]}
    binds = [bindparam("excluded", expanding=True)]

    if servers is not None:
        if not servers:
            return []
        sql += "AND `server` IN :servers "
        params["servers"] = servers
        binds.append(bindparam("servers", expanding=True))

    # Today's DTE mode. Stored values vary in case and padding ('Daily',
    # 'DAILY'), so both sides are folded before comparing.
    for key, column in DTE_COLUMNS.items():
        allowed = dte.get(key)
        if not allowed:
            continue
        sql += f"AND LOWER(TRIM(COALESCE(`{column}`, ''))) IN :{key} "
        params[key] = allowed
        binds.append(bindparam(key, expanding=True))

    sql += "ORDER BY `algo`, `server`, `SubCategory`, `userId`"
    statement = text(sql).bindparams(*binds)
    return [dict(r) for r in db.session.execute(statement, params).mappings().all()]


def _blank_counts() -> dict[str, Any]:
    return {"users": 0, **{c: 0 for c in CATEGORIES}}


def _tally(node: dict[str, Any], row: dict[str, Any]) -> None:
    node["users"] += 1
    category = str(row.get("Category") or "").strip().upper()
    if category in CATEGORIES:
        node[category] += 1


def build(
    on_date: dt.date,
    servers: list[str] | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """The pivot tree for `on_date`, narrowed to today's DTE mode.

    Args:
        on_date: the date to report on.
        servers: None for no restriction, else the servers to include.
        mode: DTE mode; defaults to the one in force on `on_date` - the manual
            pin from Admin Controls if there is one, else the weekly schedule.

    Returns:
        {"date", "mode", "source", "dte", "totals", "rows": [algo -> ... -> user]}
    """
    state = rules_io.mode_state(on_date)
    source = "manual" if mode else state["source"]
    mode = mode or state["mode"]
    dte = rules_io.dte_filter(mode)
    rows = _rows(on_date, servers, dte)

    totals = _blank_counts()
    algos: dict[str, dict[str, Any]] = {}

    for row in rows:
        algo = str(row.get("algo") or "").strip()
        server = str(row.get("server") or "").strip() or "(no server)"
        sub = str(row.get("SubCategory") or "").strip().upper() or "(none)"

        node = algos.setdefault(
            algo, {"name": algo, "kind": "algo", **_blank_counts(), "children": {}}
        )
        server_node = node["children"].setdefault(
            server, {"name": server, "kind": "server", **_blank_counts(), "children": {}}
        )
        sub_node = server_node["children"].setdefault(
            sub, {"name": sub, "kind": "subcategory", **_blank_counts(), "children": []}
        )

        for target in (totals, node, server_node, sub_node):
            _tally(target, row)

        sub_node["children"].append(
            {
                "name": f"{row['userId']} ({row.get('alias') or ''})".strip(),
                "kind": "user",
                "users": 1,
                **{c: 0 for c in CATEGORIES},
                "children": [],
            }
        )

    logger.info(
        "Dashboard pivot for %s in %s mode (%s): %s user(s) across %s algo(s)",
        on_date, mode, source, totals["users"], len(algos),
    )

    return {
        "date": on_date.isoformat(),
        "mode": mode,
        "source": source,
        "weekday": state["weekday"],
        # What the mode admits, so the page can say why users are missing.
        "dte": rules_io.dte_text(mode),
        "totals": totals,
        "rows": _sorted(algos),
    }


def _sorted(nodes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Depth-first sort: numeric names numerically, the rest alphabetically."""
    def key(node: dict[str, Any]):
        name = node["name"]
        try:
            return (0, float(name), "")
        except (TypeError, ValueError):
            return (1, 0.0, name)

    out = []
    for node in sorted(nodes.values(), key=key):
        children = node.get("children")
        if isinstance(children, dict):
            node["children"] = _sorted(children)
        out.append(node)
    return out
