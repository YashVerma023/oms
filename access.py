"""Role-based access: what the signed-in user may see and do.

Every rule lives here rather than being scattered through routes and
templates, so the answer to "can an operator do X?" has exactly one source.

Operators are scoped to the servers assigned to them: their login `name` is
matched against `server_config.Operator`. An operator with no matching servers
sees nothing on the scoped tabs - it fails closed rather than falling back to
showing everything.
"""

from __future__ import annotations

import logging

from flask import session
from sqlalchemy import text

from database.db import db

logger = logging.getLogger(__name__)

ADMIN_ROLES = ("admin", "superadmin")
OPERATOR = "operator"


def role() -> str:
    return session.get("role", "")


def is_operator() -> bool:
    return role() == OPERATOR


def is_admin() -> bool:
    return role() in ADMIN_ROLES


# ---------------------------------------------------------------------------
# What an operator is allowed to reach
# ---------------------------------------------------------------------------

# Upload targets an operator may not use.
OPERATOR_BLOCKED_UPLOADS = ("server-config", "jainam")

# Tabs an operator may edit. Server Config is theirs to maintain; Running is a
# read-only snapshot of what the algos report.
OPERATOR_EDITABLE_PAGES = ("server-config", "all-users", "usersetting")


def can_delete() -> bool:
    """Operators never delete rows, on any tab."""
    return not is_operator()


def can_edit(page_key: str) -> bool:
    if not is_operator():
        return True
    return page_key in OPERATOR_EDITABLE_PAGES


def can_upload(target: str) -> bool:
    if not is_operator():
        return True
    return target not in OPERATOR_BLOCKED_UPLOADS


def can_open_controls() -> bool:
    """Admin Controls - rules and Save All Users - is admin only."""
    return is_admin()


def locked_to_today(page_key: str) -> bool:
    """Operators see only today on the dated tabs; no history browsing."""
    return is_operator() and page_key == "all-users"


# ---------------------------------------------------------------------------
# Which servers an operator owns
# ---------------------------------------------------------------------------

def operator_servers() -> list[str] | None:
    """Servers assigned to the signed-in operator.

    Returns:
        None for admin and superadmin, meaning "no restriction".
        A list of server names for an operator - possibly empty, which means
        they see nothing on the scoped tabs.
    """
    if not is_operator():
        return None

    name = (session.get("name") or "").strip()
    if not name:
        return []

    rows = db.session.execute(
        text(
            "SELECT `Server` FROM `server_config` "
            "WHERE UPPER(TRIM(`Operator`)) = UPPER(:name)"
        ),
        {"name": name},
    ).scalars().all()

    servers = [str(s).strip() for s in rows if str(s or "").strip()]
    if not servers:
        logger.warning(
            "Operator '%s' has no servers in server_config - scoped tabs will "
            "be empty", name,
        )
    return servers
