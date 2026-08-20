"""Cross-table derived values.

Columns that are copied from another table rather than entered or uploaded.
Each function is idempotent and safe to run repeatedly: it re-derives from the
source table, so running it twice changes nothing the second time.
"""

from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache
from typing import Any

from sqlalchemy import text

from core import rules
from database.db import db

logger = logging.getLogger(__name__)

# The authoritative rows are the most recent date that is not in the future.
_MAX_DATE = "(SELECT MAX(`Date`) FROM `all_users` WHERE `Date` <= CURDATE())"
CURRENT = f"`Date` = {_MAX_DATE}"
CURRENT_A = f"`Date` = {_MAX_DATE}"


# ---------------------------------------------------------------------------
# all_users."Operator Name"  <-  server_config.Operator, matched on server
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def operator_map() -> dict[str, str]:
    """server (uppercased) -> operator, from the server_config mapping table.

    Cached because it is consulted once per row during an import. Call
    `operator_map.cache_clear()` after anything that changes server_config.
    """
    rows = db.session.execute(
        text("SELECT `Server`, `Operator` FROM `server_config`")
    ).all()

    mapping = {
        str(server).strip().upper(): str(operator).strip()
        for server, operator in rows
        if server is not None and str(operator or "").strip()
    }
    logger.debug("Loaded %s server->operator mappings", len(mapping))
    return mapping


def operator_for(server: Any, mapping: dict[str, str]) -> str:
    """The operator a row should show for its server.

    Inactive servers report themselves; an unmapped server reports
    NOT RUNNING, since no operator is responsible for it.
    """
    token = str(server or "").strip().upper()

    for state in rules.INACTIVE_STATES:
        if token == state.upper():
            return state

    return mapping.get(token) or rules.NOT_RUNNING


def apply_all_users(record: dict[str, Any]) -> dict[str, Any]:
    """core.rules.apply plus the derived operator. Used by every write path."""
    rules.apply(record)
    record["Operator Name"] = operator_for(record.get("server"), operator_map())
    # The sheet carries no Date; uploaded and newly created rows are today's.
    if not record.get("Date"):
        record["Date"] = dt.date.today()
    return record


def sync_all_users_operator() -> int:
    """Re-derive `Operator Name` for every all_users row.

    Skipped when server_config is empty: with no mapping, every row would be
    rewritten to NOT RUNNING and the existing operators lost.

    Returns:
        Number of rows whose operator changed.
    """
    operator_map.cache_clear()
    mapping = operator_map()

    if not mapping:
        logger.warning(
            "server_config has no operators - skipping the operator sync so "
            "existing values in all_users are not overwritten"
        )
        return 0

    rows = db.session.execute(
        text("SELECT `userId`, `server`, `Operator Name` FROM `all_users` "
             f"WHERE {CURRENT}")
    ).mappings().all()

    updates = [
        {"pk": row["userId"], "op": operator_for(row["server"], mapping)}
        for row in rows
        if (row["Operator Name"] or "") != operator_for(row["server"], mapping)
    ]

    if updates:
        try:
            db.session.execute(
                text("UPDATE `all_users` SET `Operator Name` = :op "
                 f"WHERE `userId` = :pk AND {CURRENT}"),
                updates,
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Deriving all_users.Operator Name failed")
            raise

    logger.info(
        "Derived all_users.Operator Name from server_config: %s of %s row(s) updated",
        len(updates), len(rows),
    )
    return len(updates)


def sync_usersetting_algo() -> int:
    """Copy `all_users.algo` onto `usersetting.algo`, matched on user id.

    Matched per user, not per server: in all_users a server can run more than
    one algo (VS21 has two, NOT RUNNING has four), so a server-level lookup
    would be ambiguous. `usersetting.User ID` maps 1:1 onto `all_users.userId`.

    Users absent from all_users keep whatever they already had - their algo is
    simply not derivable.

    Returns:
        Number of usersetting rows whose algo changed.
    """
    statement = text(
        "UPDATE `usersetting` AS u "
        "JOIN `all_users` AS a ON a.`userId` = u.`User ID` "
        f"AND a.{CURRENT_A} "
        "SET u.`algo` = a.`algo` "
        # Only touch rows that actually differ, so rowcount is meaningful and
        # `updated_at` is not bumped on every run.
        "WHERE NOT (u.`algo` <=> a.`algo`)"
    )

    try:
        changed = db.session.execute(statement).rowcount
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Deriving usersetting.algo from all_users failed")
        raise

    unmatched = db.session.execute(
        text(
            "SELECT COUNT(*) FROM `usersetting` u "
            "LEFT JOIN `all_users` a ON a.`userId` = u.`User ID` "
            f"AND a.{CURRENT_A} "
            "WHERE a.`userId` IS NULL"
        )
    ).scalar()

    logger.info(
        "Derived usersetting.algo from all_users: %s row(s) updated, "
        "%s row(s) have no matching user",
        changed, unmatched,
    )
    return changed
