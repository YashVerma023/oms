"""Read/write helpers for the `login` table (portal users)."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text

from database.db import db

logger = logging.getLogger(__name__)

# Roles the portal recognises. Kept here so the add-user form and the access
# decorators agree on one list.
ROLES = ("superadmin", "admin", "operator", "crm")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def list_logins() -> list[dict[str, Any]]:
    # Sorted by the ROLES order above rather than a second hardcoded list, so
    # adding or removing a role cannot leave the two out of step.
    order = ", ".join(f"'{r}'" for r in ROLES)
    rows = db.session.execute(
        text(
            "SELECT `id`, `role`, `name`, `email`, `password`, `created_at` "
            f"FROM `login` ORDER BY FIELD(`role`, {order}), `name`"
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def create_login(role: str, name: str, email: str, password: str) -> None:
    """Add a portal user.

    Raises:
        ValueError: a field is missing, the role is unknown, the email is
            malformed, or that email is already registered.
    """
    role = (role or "").strip()
    name = (name or "").strip()
    email = (email or "").strip().lower()
    password = password or ""

    if not all((role, name, email, password)):
        raise ValueError("All four fields are required.")
    if role not in ROLES:
        raise ValueError(f"Unknown role '{role}'. Choose one of: {', '.join(ROLES)}.")
    if not _EMAIL.match(email):
        raise ValueError(f"'{email}' is not a valid email address.")

    exists = db.session.execute(
        text("SELECT `id` FROM `login` WHERE LOWER(`email`) = :email"), {"email": email}
    ).first()
    if exists:
        raise ValueError(f"A user with email '{email}' already exists.")

    try:
        db.session.execute(
            text(
                "INSERT INTO `login` (`role`, `name`, `email`, `password`) "
                "VALUES (:role, :name, :email, :password)"
            ),
            {"role": role, "name": name, "email": email, "password": password},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to create login '%s'", email)
        raise

    logger.info("Created login '%s' with role '%s'", email, role)
